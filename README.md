# Oracle ARM VM Hunter

Automated script to request Oracle Cloud ARM VM (VM.Standard.A1.Flex) instances 24/7 using GitHub Actions, with real-time status notifications sent directly to Telegram.

## Features
* Sends instance creation requests every 5 minutes using GitHub Actions (runs entirely in the cloud).
* Silently ignores "Out of capacity" errors to prevent spam, notifying Telegram only on success or critical configuration failures.
* Automatically falls back to search for the virtual cloud network (VCN) and subnet in the root compartment (Tenancy) if they are not found in the local compartment.

## Prerequisites
1. **Oracle Cloud Infrastructure (OCI) Account**: An active account with a VCN named `hari-network` and a subnet named `public subnet-hari-network`.
2. **SSH Key Pair**: Your SSH public key should be configured directly in `hunter.py` (`ssh_authorized_keys`). Keep the corresponding private key on your local machine to connect later.
3. **Telegram Bot**: A Telegram bot created via BotFather, along with your bot token and chat ID.

## GitHub Setup Instructions

### 1. Configure Secrets
Go to your repository on GitHub -> **Settings** -> **Secrets and variables** -> **Actions** -> Click **New repository secret** to add the following:

| Secret Name | Description | Example |
| :--- | :--- | :--- |
| `OCI_USER_OCID` | OCID of your user | `ocid1.user.oc1..aaaaaaa...` |
| `OCI_TENANCY_OCID` | OCID of your tenancy (root) | `ocid1.tenancy.oc1..aaaaaaa...` |
| `OCI_COMPARTMENT_OCID` | OCID of your target compartment | Same as Tenancy OCID if using Root |
| `OCI_FINGERPRINT` | API Key fingerprint | `aa:bb:cc:dd:ee:...` |
| `OCI_REGION` | OCI Region | `ap-singapore-1` |
| `OCI_PRIVATE_KEY` | Content of your OCI API private key file (`.pem`) | RSA private key (with headers, no extra spaces) |
| `TELEGRAM_TO_TOKEN` | Telegram bot token | `123456789:ABCdefGhI...` |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | `987654321` |

### 2. Enable Unlimited Runs
* By default, the workflow is scheduled to run every 5 minutes.
* To avoid hitting the 2,000-minute free monthly limit for private repositories on GitHub Free plans, change the repository visibility to **Public** under **Settings > Danger Zone**. GitHub Actions runs are completely free and unlimited for public repositories (your API keys and configurations in Secrets will remain securely encrypted and hidden).

### 3. Disabling After Success
Once the Telegram bot sends a success notification, go to the **Actions** tab on your GitHub repository -> select the **Oracle Cloud ARM VM Hunter** workflow -> click the `...` menu on the top right -> select **Disable workflow** to stop the script.