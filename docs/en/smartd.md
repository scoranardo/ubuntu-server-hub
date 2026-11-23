# smartd: alerts for failing disks

**English** · [Português](../pt-BR/smartd.md) · [← README](../../README.md)

Disks (HDD, SSD, NVMe) record their own health in **S.M.A.R.T.**: reallocated sectors, read errors, temperature, wear.
`smartd` (package `smartmontools`) watches that data, runs periodic self-tests and calls a script when something gets worse.
This guide makes smartd report to the hub's **Backups & disks** card.

## 1. Install

```bash
sudo apt install --no-install-recommends smartmontools
```

`--no-install-recommends` stops apt from installing a mail server (postfix), which is not needed here.

## 2. Get to know your disks

```bash
lsblk -d -o NAME,MODEL,SIZE,ROTA,TRAN      # ROTA=1 means HDD; TRAN shows sata/nvme/usb
sudo smartctl -i /dev/sda                  # model, serial, whether SMART is enabled
sudo smartctl -H /dev/sda                  # overall assessment: PASSED / FAILED
sudo smartctl -A /dev/sda                  # attributes (watch Reallocated_Sector_Ct, Pending, CRC)
```

USB disks sometimes need `-d sat` (e.g. `sudo smartctl -d sat -H /dev/sdb`). Some USB adapters do not pass SMART through at all.

## 3. Install the hub scripts

From the hub folder (skip what you already did in the Backrest guide):

```bash
sudo install -m 755 scripts/hub-notify /usr/local/bin/hub-notify
sudo install -m 755 scripts/smartd-hub-notify /usr/local/bin/smartd-hub-notify
echo "HUB_TOKEN_FILE=$PWD/data/notify_token" | sudo tee /etc/hub-notify.env
```

They are **copies** on purpose: smartd runs as root, so the script it executes must not be a file your user can edit.

## 4. Configure smartd

```bash
sudo cp /etc/smartd.conf /etc/smartd.conf.orig
sudo nano /etc/smartd.conf
```

Comment out (with `#`) the existing `DEVICESCAN` line and add this at the end, **on a single line**:

```
DEVICESCAN -a -o on -S on -n standby,q -s (S/../.././02|L/../../6/03) -W 4,45,55 -m <nomailer> -M daily -M exec /usr/local/bin/smartd-hub-notify
```

| Option | |
|---|---|
| `DEVICESCAN` | watch every disk found |
| `-a` | all default checks (health, attributes, error log, self-tests) |
| `-o on -S on` | offline data collection and attribute autosave |
| `-n standby,q` | do not spin up a sleeping HDD just to check it |
| `-s (S/../.././02\|L/../../6/03)` | **short** test every day at 2 AM, **long** test every Saturday at 3 AM |
| `-W 4,45,55` | report a 4 °C change, inform at 45 °C, alert at 55 °C |
| `-m <nomailer>` | no e-mail, only `-M exec` |
| `-M daily` | if the problem persists, repeat the alert once a day |
| `-M exec …` | run the script that sends the alert to the hub |

For a USB disk that needs `-d sat`, replace `DEVICESCAN` with one line per disk, using a stable name:

```
/dev/disk/by-id/usb-Vendor_Model_123456-0:0 -d sat -a -o on -S on -n standby,q -s (S/../.././02|L/../../6/03) -W 4,45,55 -m <nomailer> -M daily -M exec /usr/local/bin/smartd-hub-notify
```

## 5. Test it

Temporarily add `-M test` to the end of the line, then restart the service:

```bash
sudo systemctl restart smartmontools
journalctl -u smartmontools -n 30 --no-pager
```

Each disk should show up as a **Disco /dev/…** item marked *ok* in **Backups & disks**. Its message is in Portuguese and means "smartd test: notification working".
Then **remove `-M test`**, restart again and delete the test items with **✕**.

## 6. When an alert arrives

| Type | Hub level | What to do |
|---|---|---|
| `Health` (FAILED) | error | the disk predicts its own failure: **check your backup now** and replace the disk |
| `CurrentPendingSector`, `OfflineUncorrectableSector` | error | bad sectors. Run `sudo smartctl -t long /dev/sdX` and keep watching; if the count grows, replace the disk |
| `ErrorCount`, `SelfTest` | warning | check `sudo smartctl -a /dev/sdX`. CRC errors usually mean a bad **cable** |
| `Temperature` | warning | airflow, dust, disks packed too close |

After checking, click **✓** in the hub to mark it resolved. If the problem is still there, `-M daily` reports it again the next day.
