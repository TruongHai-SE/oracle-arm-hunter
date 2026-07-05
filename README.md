# Oracle ARM VM Hunter

An automated tool utilizing the OCI Python SDK and GitHub Actions to continuously request Oracle Cloud Always-Free ARM instances (`VM.Standard.A1.Flex`) 24/7. Sends real-time status notifications and copy-paste SSH commands directly to Telegram.

---

## How It Works

* **Continuous Execution:** GitHub Actions triggers the hunter script every 5 minutes using a cron schedule.
* **Smart Error Suppression:** Capacity limits (`Out of capacity`) are silently ignored to prevent spam, notifying Telegram only on successful creation or critical auth/configuration failures.
* **Public IP Resolution:** Once the VM is provisioned, the script automatically queries the OCI VNIC attachments, retrieves the public IP, and compiles a ready-to-use SSH connection string.

---

## Configuration & Setup

### 1. SSH & Network Configuration
Before deploying, ensure you have configured your public SSH key, VCN name, and Subnet name directly in your `hunter.py` script.

### 2. GitHub Secrets
Add the following secrets to your GitHub repository under **Settings > Secrets and variables > Actions > New repository secret**:

| Secret Name | Description |
| :--- | :--- |
| `OCI_USER_OCID` | OCID of your OCI user |
| `OCI_TENANCY_OCID` | OCID of your root tenancy |
| `OCI_COMPARTMENT_OCID` | OCID of your target compartment |
| `OCI_FINGERPRINT` | OCI API Key fingerprint |
| `OCI_REGION` | OCI Region (e.g., `ap-singapore-1`) |
| `OCI_PRIVATE_KEY` | Content of your OCI API private key file (`.pem`) |
| `TELEGRAM_TO_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

---

## Telegram Notification Preview

When the instance is successfully created, you will receive a Telegram message formatted like this:

```markdown
🚀 ORACLE CLOUD - VM CREATED SUCCESSFULLY
──────────────────────────────
🟢 Status: RUNNING
🖥️ Instance Name: hari-VM
🌐 Region: ap-singapore-1
🌐 Public IP: 152.xx.xx.xx

📦 Hardware Configuration:
• Shape: VM.Standard.A1.Flex
• Resources: 2 OCPUs / 12 GB RAM
• Boot Volume: 150 GB

💿 Operating System:
• Image: Canonical-Ubuntu-24.04-Minimal-aarch64
• Username: ubuntu

🔑 SSH Connection:
ssh ubuntu@152.xx.xx.xx
──────────────────────────────
🔗 Access OCI Console
```

---

> [!NOTE]
> Once the instance is successfully created, remember to **disable the workflow** under the Actions tab in your GitHub repository to stop duplicate API requests.