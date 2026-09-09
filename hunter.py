import os
import sys
import time
import tempfile
import requests
import oci

def send_telegram(message, token, chat_id):
    if not token or not chat_id:
        print("Telegram token or chat ID is missing. Skipping Telegram notification.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Telegram notification: {e}")

def retry_call(api_func, *args, max_retries=3, delay=5, **kwargs):
    """Retries a transient API call up to max_retries times before raising."""
    for attempt in range(1, max_retries + 1):
        try:
            return api_func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries:
                raise e
            print(f"Transient network issue during setup ({e}). Retrying ({attempt}/{max_retries}) in {delay}s...")
            time.sleep(delay)

def main():
    # Load settings from environment variables
    user_ocid = os.environ.get("OCI_USER_OCID")
    tenancy_ocid = os.environ.get("OCI_TENANCY_OCID")
    compartment_ocid = os.environ.get("OCI_COMPARTMENT_OCID")
    fingerprint = os.environ.get("OCI_FINGERPRINT")
    region = os.environ.get("OCI_REGION", "ap-singapore-1")
    private_key_pem = os.environ.get("OCI_PRIVATE_KEY")
    
    telegram_token = os.environ.get("TELEGRAM_TO_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    # Validate mandatory configuration parameters
    missing_params = []
    if not user_ocid: missing_params.append("OCI_USER_OCID")
    if not tenancy_ocid: missing_params.append("OCI_TENANCY_OCID")
    if not compartment_ocid: missing_params.append("OCI_COMPARTMENT_OCID")
    if not fingerprint: missing_params.append("OCI_FINGERPRINT")
    if not private_key_pem: missing_params.append("OCI_PRIVATE_KEY")

    if missing_params:
        error_msg = (
            f"⚠️ *ORACLE CLOUD HUNTER - CONFIGURATION ERROR*\n"
            f"──────────────────────────────\n"
            f"❌ *Missing Variables:*\n"
            + "\n".join([f"• `{param}`" for param in missing_params]) + "\n"
            f"──────────────────────────────\n"
            f"⚙️ *Action Required:* Please add these secrets in your GitHub Repository settings."
        )
        print(error_msg)
        send_telegram(error_msg, telegram_token, telegram_chat_id)
        sys.exit(1)

    # Write key to a temporary file as OCI SDK from_file expects a file path
    # or config dict with key_file key pointing to a path.
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as key_file:
        key_file.write(private_key_pem.strip() + "\n")
        key_file_path = key_file.name

    config = {
        "user": user_ocid,
        "fingerprint": fingerprint,
        "tenancy": tenancy_ocid,
        "region": region,
        "key_file": key_file_path
    }

    try:
        # Initialize clients with resilient timeouts (30s connect, 60s read)
        # Prevents premature ConnectTimeoutError due to international latency spikes
        timeout_config = (30.0, 60.0)
        identity_client = oci.identity.IdentityClient(config)
        identity_client.base_client.timeout = timeout_config

        compute_client = oci.core.ComputeClient(config)
        compute_client.base_client.timeout = timeout_config

        vcn_client = oci.core.VirtualNetworkClient(config)
        vcn_client.base_client.timeout = timeout_config

        # Check if target instance already exists and is active
        print("Checking if 'hari-VM' is already running...")
        existing_instances = retry_call(compute_client.list_instances, compartment_id=compartment_ocid).data
        for inst in existing_instances:
            if inst.display_name == "hari-VM" and inst.lifecycle_state in ["PROVISIONING", "RUNNING", "STARTING"]:
                print(f"Target instance 'hari-VM' already exists with state '{inst.lifecycle_state}'. Target achieved, nothing to hunt.")
                return

        # 1. Fetch Availability Domain (AD-1)
        print("Fetching Availability Domains...")
        ads = retry_call(identity_client.list_availability_domains, compartment_id=compartment_ocid).data
        ad_name = None
        for ad in ads:
            if "ad-1" in ad.name.lower():
                ad_name = ad.name
                break
        if not ad_name:
            ad_name = ads[0].name
            print(f"AD-1 not found, falling back to: {ad_name}")
        else:
            print(f"Targeting AD: {ad_name}")

        # 2. Find VCN by name: "hari-network"
        print("Finding VCN 'hari-network'...")
        vcns = retry_call(vcn_client.list_vcns, compartment_id=compartment_ocid).data
        vcn_id = None
        target_network_compartment_id = compartment_ocid
        for v in vcns:
            if v.display_name == "hari-network":
                vcn_id = v.id
                break

        # Fallback to Tenancy (Root) compartment if not found in target compartment
        if not vcn_id and compartment_ocid != tenancy_ocid:
            print("VCN 'hari-network' not found in the specified compartment. Searching in Tenancy (Root)...")
            vcns = retry_call(vcn_client.list_vcns, compartment_id=tenancy_ocid).data
            for v in vcns:
                if v.display_name == "hari-network":
                    vcn_id = v.id
                    target_network_compartment_id = tenancy_ocid
                    break

        if not vcn_id:
            raise Exception("VCN 'hari-network' not found in your compartment or Root compartment. Make sure the VCN exists.")

        # 3. Find Subnet by name: "public subnet-hari-network"
        print("Finding Subnet 'public subnet-hari-network'...")
        subnets = retry_call(vcn_client.list_subnets, compartment_id=target_network_compartment_id, vcn_id=vcn_id).data
        subnet_id = None
        for s in subnets:
            if s.display_name == "public subnet-hari-network":
                subnet_id = s.id
                break
        if not subnet_id:
            raise Exception("Subnet 'public subnet-hari-network' not found in VCN 'hari-network'.")

        # 4. Search for canonical Ubuntu 24.04 Minimal aarch64 image
        print("Searching for Ubuntu 24.04 Minimal aarch64 image...")
        images = retry_call(
            compute_client.list_images,
            compartment_id=compartment_ocid,
            shape="VM.Standard.A1.Flex"
        ).data

        target_image = None
        # Order of preference: Ubuntu 24.04 Minimal aarch64
        matched_images = []
        for img in images:
            name = img.display_name.lower()
            if "ubuntu" in name and "24.04" in name and "minimal" in name and "aarch64" in name:
                matched_images.append(img)

        # Fallback to general ubuntu 24.04 aarch64 if minimal is not directly matched
        if not matched_images:
            for img in images:
                name = img.display_name.lower()
                if "ubuntu" in name and "24.04" in name and "aarch64" in name:
                    matched_images.append(img)

        if not matched_images:
            raise Exception("No Ubuntu 24.04 aarch64 image found for shape VM.Standard.A1.Flex.")

        # Pick the newest image based on creation date
        matched_images.sort(key=lambda x: x.time_created, reverse=True)
        target_image = matched_images[0]
        print(f"Selected Image: {target_image.display_name} (ID: {target_image.id})")

        # 5. Build launch details
        ssh_key = (
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC50vPeOXQoNid1tw2un0p7J6h39tcC21bwnnOghc/9PmWhwms"
            "Cfwkmsrr88vCYLBHNC0u+o0fJiEZ0CA3L1aKGXWknmHv/Jbqk8+a3cu7I4V2HMjqCD+Vw/xe8M0xvKKqua2Ei85"
            "URxF9Cec6G7ISsBEVbYwuswEaMdacX0keLc6hL8J0Gdhr7BlTqGu+r894sFprRXHLYCw6JS3hN6C0x7IEbA1xL4"
            "3lEES1mihPBAht4nxj/aPXHuwYWiW0Hhi5eEh1FP7oR2w42/uGV1Xu/J/YvGtK6M7a/b2HfdplocaFC4tVn+WDR"
            "m0mRg26jwNwa203Xf5owzkvfJTy3M18h ssh-key-2026-07-05"
        )

        launch_instance_details = oci.core.models.LaunchInstanceDetails(
            display_name="hari-VM",
            compartment_id=compartment_ocid,
            availability_domain=ad_name,
            shape="VM.Standard.A1.Flex",
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=2.0,
                memory_in_gbs=12.0
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=target_image.id,
                boot_volume_size_in_gbs=150
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id,
                assign_public_ip=True,
                display_name="hari-VM-vnic"
            ),
            metadata={
                "ssh_authorized_keys": ssh_key
            }
        )

        # 6. Hunting Loop with backoff & retry
        max_runtime = int(os.environ.get("HUNTER_MAX_RUNTIME", 240))  # Run up to 4 minutes per workflow
        retry_delay = int(os.environ.get("HUNTER_RETRY_DELAY", 25))    # 25 seconds interval (safe, non-spam)
        rate_limit_delay = 45                                          # Backoff on 429 TooManyRequests
        
        start_time = time.time()
        attempt = 0
        instance = None

        print(f"Starting hunting loop (max {max_runtime}s, {retry_delay}s interval)...")

        while True:
            attempt += 1
            elapsed = time.time() - start_time
            if elapsed >= max_runtime:
                print(f"Workflow hunting window ({max_runtime}s) reached after {attempt - 1} attempts. Handing over to next cycle.")
                break

            print(f"[{time.strftime('%H:%M:%S')}] Attempt #{attempt}: Requesting VM launch...")
            try:
                response = compute_client.launch_instance(launch_instance_details=launch_instance_details)
                instance = response.data
                print(f"SUCCESS: Instance launched! ID: {instance.id}")
                break
            except oci.exceptions.ServiceError as e:
                error_str = str(e).lower()
                e_code = (e.code or "").lower()

                # Check if it's a tenancy limit/quota issue (FATAL)
                is_limit_exceeded = "limitexceeded" in e_code or "limit exceeded" in error_str

                # Check if it's OCI rate limit (HTTP 429)
                is_rate_limit = e.status == 429 or "toomanyrequests" in e_code or "too many requests" in error_str

                # Filter for typical physical host capacity errors (RETRYABLE)
                is_capacity = (
                    e.status == 500 or
                    "outofhostcapacity" in e_code or
                    any(phrase in error_str for phrase in ["out of capacity", "out of host capacity", "capacity"])
                ) and not is_limit_exceeded and not is_rate_limit

                if is_capacity:
                    print(f"[{time.strftime('%H:%M:%S')}] Attempt #{attempt}: Out of capacity at AD-1. Request ignored silently.")
                    remaining = max_runtime - (time.time() - start_time)
                    if remaining < retry_delay:
                        print(f"Hunting window ending ({remaining:.1f}s remaining < {retry_delay}s). Exiting cleanly for next cycle.")
                        break
                    time.sleep(retry_delay)
                elif is_rate_limit:
                    print(f"[{time.strftime('%H:%M:%S')}] Attempt #{attempt}: OCI Rate Limit hit (429). Backing off for {rate_limit_delay}s...")
                    remaining = max_runtime - (time.time() - start_time)
                    if remaining < rate_limit_delay:
                        break
                    time.sleep(rate_limit_delay)
                elif is_limit_exceeded:
                    err_msg = (
                        f"⚠️ *ORACLE CLOUD HUNTER - LIMIT EXCEEDED*\n"
                        f"──────────────────────────────\n"
                        f"🚫 *Status:* `{e.status}`\n"
                        f"🔑 *Code:* `{e.code}`\n"
                        f"💬 *Message:* `{e.message}`\n"
                        f"──────────────────────────────\n"
                        f"🚨 *Explanation:* Your account quota or service limit has been exceeded.\n"
                        f"• Please check your OCI Console -> Limits, Quotas and Usage.\n"
                        f"• Ensure you haven't reached the 200 GB Always-Free boot volume limit.\n"
                        f"• Ensure your account has remaining ARM OCPU/RAM quota.\n"
                        f"⚙️ *Action Required:* The hunter script has stopped. Please resolve your tenancy limits."
                    )
                    print(f"LimitExceeded: {err_msg}")
                    send_telegram(err_msg, telegram_token, telegram_chat_id)
                    sys.exit(1)
                else:
                    # Report other actual failures (like auth issues, wrong config)
                    err_msg = (
                        f"⚠️ *ORACLE CLOUD HUNTER - SERVICE ERROR*\n"
                        f"──────────────────────────────\n"
                        f"🚫 *Status:* `{e.status}`\n"
                        f"🔑 *Code:* `{e.code}`\n"
                        f"💬 *Message:* `{e.message}`\n"
                        f"──────────────────────────────\n"
                        f"⚙️ *Action Required:* Please review your compartment permissions or configuration."
                    )
                    print(f"ServiceError: {err_msg}")
                    send_telegram(err_msg, telegram_token, telegram_chat_id)
                    sys.exit(1)
            except Exception as e:
                # Transient network error during launch call
                print(f"[{time.strftime('%H:%M:%S')}] Attempt #{attempt}: Transient connection error during launch: {e}")
                remaining = max_runtime - (time.time() - start_time)
                if remaining < retry_delay:
                    break
                time.sleep(retry_delay)

        # 7. Try to fetch Public IP (if VM was successfully created)
        if instance:
            print("Fetching Public IP address...")
            public_ip = "Allocating..."
            for i in range(8):
                try:
                    attachments = compute_client.list_vnic_attachments(
                        compartment_id=compartment_ocid,
                        instance_id=instance.id
                    ).data
                    if attachments:
                        vnic_id = attachments[0].vnic_id
                        vnic = vcn_client.get_vnic(vnic_id=vnic_id).data
                        if vnic.public_ip:
                            public_ip = vnic.public_ip
                            break
                except Exception as e:
                    print(f"Attempt {i+1}: Error fetching VNIC details: {e}")
                time.sleep(3)

            success_msg = (
                f"🚀 *ORACLE CLOUD - VM CREATED SUCCESSFULLY*\n"
                f"──────────────────────────────\n"
                f"🟢 *Status:* `{instance.lifecycle_state}`\n"
                f"🖥️ *Instance Name:* `{instance.display_name}`\n"
                f"🌐 *Region:* `{region}`\n"
                f"🌐 *Public IP:* `{public_ip}`\n\n"
                f"📦 *Hardware Configuration:*\n"
                f"• *Shape:* `{instance.shape}`\n"
                f"• *Resources:* `2 OCPUs` / `12 GB RAM`\n"
                f"• *Boot Volume:* `150 GB`\n\n"
                f"💿 *Operating System:*\n"
                f"• *Image:* `{target_image.display_name}`\n"
                f"• *Username:* `ubuntu`\n\n"
                f"🔑 *SSH Connection:*\n"
                f"`ssh ubuntu@{public_ip}`\n"
                f"──────────────────────────────\n"
                f"🔗 [Access OCI Console](https://cloud.oracle.com/?region={region})"
            )
            print("SUCCESS: VM has been created!")
            print(success_msg)
            send_telegram(success_msg, telegram_token, telegram_chat_id)

    except oci.exceptions.ServiceError as e:
        error_str = str(e).lower()
        is_limit_exceeded = "limitexceeded" in error_str or "limit exceeded" in error_str
        if is_limit_exceeded:
            err_msg = (
                f"⚠️ *ORACLE CLOUD HUNTER - LIMIT EXCEEDED*\n"
                f"──────────────────────────────\n"
                f"🚫 *Status:* `{e.status}`\n"
                f"🔑 *Code:* `{e.code}`\n"
                f"💬 *Message:* `{e.message}`\n"
                f"──────────────────────────────\n"
                f"🚨 *Explanation:* Your account quota or service limit has been exceeded.\n"
                f"• Please check your OCI Console -> Limits, Quotas and Usage.\n"
                f"• Ensure you haven't reached the 200 GB Always-Free boot volume limit.\n"
                f"• Ensure your account has remaining ARM OCPU/RAM quota.\n"
                f"⚙️ *Action Required:* The hunter script has stopped. Please resolve your tenancy limits."
            )
            print(f"LimitExceeded: {err_msg}")
            send_telegram(err_msg, telegram_token, telegram_chat_id)
            sys.exit(1)
        else:
            err_msg = (
                f"⚠️ *ORACLE CLOUD HUNTER - SERVICE ERROR*\n"
                f"──────────────────────────────\n"
                f"🚫 *Status:* `{e.status}`\n"
                f"🔑 *Code:* `{e.code}`\n"
                f"💬 *Message:* `{e.message}`\n"
                f"──────────────────────────────\n"
                f"⚙️ *Action Required:* Please review your compartment permissions or configuration."
            )
            print(f"ServiceError: {err_msg}")
            send_telegram(err_msg, telegram_token, telegram_chat_id)
            sys.exit(1)

    except Exception as e:
        err_msg = (
            f"❌ *ORACLE CLOUD HUNTER - CRITICAL FAILURE*\n"
            f"──────────────────────────────\n"
            f"🚨 *An unexpected error occurred:*\n"
            f"```\n{str(e)}\n```\n"
            f"──────────────────────────────\n"
            f"⚙️ *Action Required:* Inspect the GitHub Actions runner log to diagnose."
        )
        print(err_msg)
        send_telegram(err_msg, telegram_token, telegram_chat_id)
        sys.exit(1)

    finally:
        # Clean up key file
        if os.path.exists(key_file_path):
            os.remove(key_file_path)

if __name__ == "__main__":
    main()
