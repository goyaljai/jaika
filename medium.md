# I Replaced My $73 AWS Bill With Two Old Android Phones

*How I turned a pair of forgotten phones into a production AI server stack — running LangChain, Gemini, and real-time voice chat — for $0/month.*

---

Last year I stared at my AWS bill for a while. $73/month. One EC2 t3.large. 8GB RAM. It ran [Jaika](https://github.com/goyaljai/jaika) — my open-source AI voice assistant — and most of the time it was sitting idle, burning money while I slept.

Then I looked at my drawer. Two old Android phones. A Motorola with a Snapdragon 778G and 8GB RAM. A Samsung with similar specs. Both collecting dust.

Here's the thing nobody talks about: **your old Android phone is a more capable computer than the server most startups deploy on.** An 8-core ARM64 CPU, 8GB RAM, a battery that acts as a free UPS — and it fits in your pocket. The only reason we don't use phones as servers is that nobody told us we could.

So I did. Both phones now serve real users. Tailscale gives them public URLs. Supervisord keeps processes alive. A boot script makes them self-healing. The app — a full AI voice assistant with LangChain pipelines, Gemini LLM, ElevenLabs TTS, and real-time voice — runs flawlessly. My AWS bill? **$0.**

Here's exactly how I did it, command by command.

---

## What We're Building

By the end of this article, you'll have:

- A full **Ubuntu Linux environment** running inside your Android phone (no custom ROM — Android keeps working normally)
- **Supervisord** managing your app processes (auto-restart on crash)
- **Tailscale** giving your phone a public HTTPS URL accessible from anywhere
- **SSH access** to your phone from any device
- A **boot script** that brings everything back up automatically after a reboot
- A **deployment pipeline** that pushes code from your laptop to your phone in seconds

The architecture:

```
    The Internet
         |
    Tailscale VPN
         |
  Android Phone (rooted)
         |
  Linux Chroot (/data/local/linux/rootfs/)
         |
    +-----------+-----------+----------+
    |           |           |          |
 Gunicorn   Tailscale    SSH       Cron
 (Flask)    (networking) (remote)  (scheduled)
    |
 Your App (LangChain + Gemini + ElevenLabs)
```

Android runs normally on top. Your Linux server lives inside a chroot — a sandboxed directory that looks and feels like a real Ubuntu machine. They coexist peacefully.

---

## What You'll Need

| Item | Why |
|---|---|
| An **old Android phone** (4GB+ RAM) | This becomes your server |
| **Root access** on the phone | We need to create chroot mounts |
| A **USB cable** | For ADB commands during setup |
| A computer with **ADB installed** | Your control plane |
| **WiFi network** | Your phone needs internet |
| ~30 minutes | That's genuinely all it takes |

Don't have root? For most phones: unlock the bootloader with `fastboot oem unlock`, flash [Magisk](https://github.com/topjohnwu/Magisk) via recovery, and you're done.

Verify root works:

```bash
adb shell "su 0 id"
# Should output: uid=0(root) gid=0(root)
```

If you see `uid=0(root)`, let's go.

---

## Step 1: Install Ubuntu (Without Replacing Android)

This is the part that surprises people. You don't flash a new OS. You don't dual-boot. You just... put Ubuntu in a folder.

Android's kernel is Linux. It has everything Ubuntu needs — `proc`, `sys`, `dev`, memory management, process scheduling. The only thing missing is the userspace (the actual programs like `bash`, `apt`, `python3`). A chroot gives you that.

```bash
# On your computer — download the official ARM64 Ubuntu base image
wget https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/ubuntu-base-22.04-base-arm64.tar.gz

# Push it to the phone
adb push ubuntu-base-22.04-base-arm64.tar.gz /data/local/tmp/

# Extract it into the chroot directory
adb shell "su 0 sh -c '
  mkdir -p /data/local/linux/rootfs
  tar -xzf /data/local/tmp/ubuntu-base-22.04-base-arm64.tar.gz -C /data/local/linux/rootfs/
  rm /data/local/tmp/ubuntu-base-22.04-base-arm64.tar.gz
'"
```

Ubuntu's entire filesystem now lives at `/data/local/linux/rootfs/` on your phone. But it can't do anything yet — it needs to be wired up to the kernel.

---

## Step 2: Wire Up the Chroot

A chroot is like a room with no windows. Programs inside can't reach the kernel interfaces they need. We give them access to a few critical ones:

- **`/proc`** — process information (every Linux program needs this)
- **`/sys`** — hardware/kernel info
- **`/dev`** — device nodes (null, random, tty)
- **DNS** — so `apt` and `curl` can resolve hostnames

```bash
adb shell "su 0 sh -c '
  ROOTFS=/data/local/linux/rootfs

  # ── /dev — THIS IS THE TRICKY PART ──
  # DO NOT bind-mount Android /dev into the chroot.
  # Android /dev has binder nodes (hwbinder, vndbinder) that the Android
  # runtime depends on. Exposing them inside the chroot causes cross-namespace
  # conflicts — it will freeze your screen and disconnect ADB.
  #
  # Instead: create a clean tmpfs with only the 7 device nodes Linux needs.
  mkdir -p $ROOTFS/dev $ROOTFS/dev/pts $ROOTFS/dev/shm
  mount -t tmpfs tmpfs $ROOTFS/dev
  mkdir -p $ROOTFS/dev/pts $ROOTFS/dev/shm

  mknod -m 666 $ROOTFS/dev/null    c 1 3
  mknod -m 666 $ROOTFS/dev/zero    c 1 5
  mknod -m 666 $ROOTFS/dev/full    c 1 7
  mknod -m 444 $ROOTFS/dev/random  c 1 8
  mknod -m 444 $ROOTFS/dev/urandom c 1 9
  mknod -m 666 $ROOTFS/dev/tty     c 5 0
  mknod -m 620 $ROOTFS/dev/ptmx    c 5 2

  ln -sf /proc/self/fd   $ROOTFS/dev/fd
  ln -sf /proc/self/fd/0 $ROOTFS/dev/stdin
  ln -sf /proc/self/fd/1 $ROOTFS/dev/stdout
  ln -sf /proc/self/fd/2 $ROOTFS/dev/stderr

  mount -o bind /dev/pts $ROOTFS/dev/pts
  mount -t tmpfs tmpfs $ROOTFS/dev/shm
  mount -t proc  proc  $ROOTFS/proc
  mount -t sysfs sysfs $ROOTFS/sys
  mount -t tmpfs tmpfs $ROOTFS/tmp
  mount -t tmpfs tmpfs $ROOTFS/run

  # DNS — Android manages this dynamically; chroot needs static resolvers
  echo "nameserver 8.8.8.8" >  $ROOTFS/etc/resolv.conf
  echo "nameserver 1.1.1.1" >> $ROOTFS/etc/resolv.conf
'"
```

**Why `tmpfs + mknod` instead of `mount -o bind /dev`?** I learned this the hard way. Bind-mounting `/dev` froze my phone, disconnected ADB, and forced a hard reboot. Android's `/dev` is a minefield of binder nodes the Android runtime depends on. The `tmpfs + mknod` approach gives you a clean `/dev` with only the nodes Linux actually needs. Safe, predictable, works across every phone I've tested.

---

## Step 3: Hello, Ubuntu

```bash
adb shell "su 0 sh -c '
  chroot /data/local/linux/rootfs /bin/bash -c \"
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export HOME=/root

    apt update
    apt install -y python3 python3-pip python3-venv openssh-server \
                   supervisor curl wget git
  \"
'"
```

You're now running `apt install` on a phone. Let that sink in.

**Important `pip` gotcha:** Android sets env vars (`ANDROID_ROOT`, `ANDROID_DATA`, etc.) that confuse pip inside the chroot. Always unset them first:

```bash
unset ANDROID_ROOT ANDROID_DATA ANDROID_ASSETS ANDROID_STORAGE
pip install flask gunicorn langchain langchain-google-genai requests
```

Yes — LangChain installs fine on ARM64. The entire pipeline runs on the phone.

---

## Step 4: Tailscale — Give Your Phone a Public URL

Your phone is on WiFi behind a router with a private IP. [Tailscale](https://tailscale.com) solves this elegantly — it creates a WireGuard VPN mesh and gives your phone a stable DNS name like `https://my-phone.tailnet-name.ts.net`, accessible from anywhere.

```bash
# Inside the chroot
curl -fsSL https://tailscale.com/install.sh | sh
```

One critical flag: `--tun=userspace-networking`. Android doesn't expose `/dev/net/tun` to the chroot (it's used by Android's VPN subsystem), so Tailscale needs userspace mode:

```bash
tailscaled --state=/var/lib/tailscale/tailscaled.state \
           --tun=userspace-networking &

sleep 5
tailscale up --ssh --accept-routes
```

After authenticating, verify:

```bash
tailscale status
# You'll see your phone listed with a 100.x.x.x IP
```

Your phone is now on the internet.

---

## Step 5: Supervisord — Keep Everything Alive

Processes crash. WiFi reconnects. Phones restart. Supervisord watches your services and restarts them automatically.

`/etc/supervisor/conf.d/myapp.conf`:

```ini
[program:myapp]
command=/opt/myapp/.venv/bin/gunicorn --bind 0.0.0.0:5244 --workers 4 --threads 4 --timeout 120 app:app
directory=/opt/myapp
environment=HOME=/root,
            PATH=/opt/myapp/.venv/bin:/usr/local/bin:/usr/bin:/bin,
            JAIKA_DATA_DIR=/opt/myapp/data
autostart=true
autorestart=true
stdout_logfile=/var/log/server/myapp-out.log
stderr_logfile=/var/log/server/myapp-err.log
```

`/etc/supervisor/conf.d/tailscaled.conf`:

```ini
[program:tailscaled]
command=/usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --tun=userspace-networking
autostart=true
autorestart=true
startsecs=5
```

```bash
mkdir -p /var/log/supervisor /var/log/server
supervisord -c /etc/supervisor/supervisord.conf
supervisorctl status
# myapp        RUNNING   pid 1234, uptime 0:00:05
# tailscaled   RUNNING   pid 1235, uptime 0:00:05
```

**Critical gotcha:** `supervisorctl restart myapp` does NOT re-read env vars from the config file. If you change environment variables, you need:

```bash
supervisorctl reread   # re-parse config files
supervisorctl update   # apply changes
```

This cost me 2 hours. Don't let it cost you.

---

## Step 6: The Boot Script — Self-Healing on Reboot

If the phone reboots, all your mounts and processes are gone. This script brings everything back automatically.

Save as `/data/local/linux/boot-auto.sh` on the phone (outside the chroot — runs in Android's namespace):

```bash
#!/system/bin/sh
LOG=/data/local/linux/boot.log
ROOTFS=/data/local/linux/rootfs
SWAPFILE=/data/local/swapfile

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> $LOG; }
log "=== BOOT SERVICE STARTING ==="

# Wait for Android to fully boot (takes 30-60s)
while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 2; done
sleep 10
log "System boot completed"

# ── Performance Tweaks ──
setenforce 0 2>/dev/null
settings put global stay_on_while_plugged_in 7     # never sleep when plugged in
settings put global wifi_sleep_policy 2            # never disable WiFi
settings put global app_standby_enabled 0          # prevent Android killing our processes
settings put global adaptive_battery_management_enabled 0
echo android-server > /sys/power/wake_lock 2>/dev/null  # prevent deep sleep
log "System tweaks applied"

# ── WiFi — wait up to 3 minutes to connect ──
svc wifi enable
sleep 10
RETRIES=0
while ! ping -c1 -W3 8.8.8.8 >/dev/null 2>&1; do
    RETRIES=$((RETRIES + 1))
    if [ $RETRIES -gt 18 ]; then
        log "WiFi not connected after 3 min. Opening settings."
        am start -a android.settings.WIFI_SETTINGS 2>/dev/null
        break
    fi
    sleep 10
done
log "WiFi: $(ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && echo connected || echo offline)"

# ── Swap — 8GB file = 16GB effective RAM ──
if [ -f "$SWAPFILE" ]; then
    swapon -p 10 "$SWAPFILE" 2>/dev/null
    log "Existing swap enabled"
else
    dd if=/dev/zero of="$SWAPFILE" bs=1M count=8192
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE"
    swapon -p 10 "$SWAPFILE" 2>/dev/null
    log "Swap created and enabled (8GB)"
fi

# ── Mount Chroot ──
mkdir -p $ROOTFS/dev $ROOTFS/dev/pts $ROOTFS/dev/shm
mount -t tmpfs tmpfs $ROOTFS/dev 2>/dev/null
mkdir -p $ROOTFS/dev/pts $ROOTFS/dev/shm
mknod -m 666 $ROOTFS/dev/null    c 1 3 2>/dev/null
mknod -m 666 $ROOTFS/dev/zero    c 1 5 2>/dev/null
mknod -m 666 $ROOTFS/dev/full    c 1 7 2>/dev/null
mknod -m 444 $ROOTFS/dev/random  c 1 8 2>/dev/null
mknod -m 444 $ROOTFS/dev/urandom c 1 9 2>/dev/null
mknod -m 666 $ROOTFS/dev/tty     c 5 0 2>/dev/null
mknod -m 620 $ROOTFS/dev/ptmx    c 5 2 2>/dev/null
ln -sf /proc/self/fd   $ROOTFS/dev/fd 2>/dev/null
ln -sf /proc/self/fd/0 $ROOTFS/dev/stdin 2>/dev/null
ln -sf /proc/self/fd/1 $ROOTFS/dev/stdout 2>/dev/null
ln -sf /proc/self/fd/2 $ROOTFS/dev/stderr 2>/dev/null
mount -o bind /dev/pts $ROOTFS/dev/pts 2>/dev/null
mount -t tmpfs tmpfs $ROOTFS/dev/shm 2>/dev/null
mount -t proc  proc  $ROOTFS/proc 2>/dev/null
mount -t sysfs sysfs $ROOTFS/sys 2>/dev/null
mount -t tmpfs tmpfs $ROOTFS/tmp 2>/dev/null
mount -t tmpfs tmpfs $ROOTFS/run 2>/dev/null
echo "nameserver 8.8.8.8" >  $ROOTFS/etc/resolv.conf
echo "nameserver 1.1.1.1" >> $ROOTFS/etc/resolv.conf
touch /data/local/linux/.mounted
log "Chroot mounted"

# ── Start All Services ──
# setsid detaches the process from init so Android can't kill it during cleanup
setsid chroot $ROOTFS /bin/bash -c "
  export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export HOME=/root
  mkdir -p /run/sshd /var/log/supervisor /var/log/server
  /usr/sbin/sshd 2>/dev/null
  /usr/sbin/cron 2>/dev/null
  /usr/bin/supervisord -c /etc/supervisor/supervisord.conf 2>/dev/null
  sleep 8
  tailscale up --ssh --accept-routes &
" < /dev/null > /dev/null 2>&1 &

sleep 15
log "All services started"
log "=== BOOT SERVICE COMPLETE ==="
```

To run on every boot, add a Magisk service trigger:

```bash
# /data/adb/service.d/boot-server.sh
#!/system/bin/sh
/data/local/linux/boot-auto.sh &
```

```bash
chmod 755 /data/adb/service.d/boot-server.sh
```

Reboot and watch:

```bash
adb shell "su 0 sh -c 'tail -f /data/local/linux/boot.log'"
```

Within 2–3 minutes of a cold boot, your server is back online.

---

## Step 7: Deploy Your App

```bash
# Package (exclude venv and cache)
tar -czf /tmp/deploy.tar.gz \
  --exclude="__pycache__" \
  --exclude=".venv" \
  --exclude="*.pyc" \
  --exclude=".env" \
  -C /path/to/your/project .

# Push
adb push /tmp/deploy.tar.gz /data/local/tmp/

# Extract and restart
adb shell "su 0 sh -c '
  tar -xzf /data/local/tmp/deploy.tar.gz -C /data/local/linux/rootfs/opt/myapp/
  rm /data/local/tmp/deploy.tar.gz
  chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart myapp
'"
```

**Deploying to multiple phones in parallel:**

```bash
D1="SERIAL_NUMBER_1"
D2="SERIAL_NUMBER_2"

for SERIAL in $D1 $D2; do
  adb -s $SERIAL push /tmp/deploy.tar.gz /data/local/tmp/
  adb -s $SERIAL shell "su 0 sh -c '
    tar -xzf /data/local/tmp/deploy.tar.gz -C /data/local/linux/rootfs/opt/myapp/
    rm /data/local/tmp/deploy.tar.gz
    chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart myapp
  '"
  echo "$SERIAL: deployed"
done
```

Entire deploy: under 5 seconds per device.

---

## The Command Cheat Sheet

### Service Management
```bash
# Status
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl status'"

# Restart app
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart myapp'"

# Reload env vars (after editing .conf)
adb shell "su 0 sh -c '
  chroot /data/local/linux/rootfs /usr/bin/supervisorctl reread
  chroot /data/local/linux/rootfs /usr/bin/supervisorctl update
'"

# View logs
adb shell "su 0 sh -c 'tail -50 /data/local/linux/rootfs/var/log/server/myapp-err.log'"
```

### Tailscale / Networking
```bash
# Status
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/tailscale status'"

# Reconnect after WiFi drop
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart tailscaled'"
sleep 8
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/tailscale up --ssh --accept-routes'"

# Internet check
adb shell "su 0 sh -c 'ping -c1 8.8.8.8 && echo ONLINE || echo OFFLINE'"
```

### System / Debugging
```bash
# Shell inside chroot
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /bin/bash'"

# RAM / swap / load
adb shell "su 0 sh -c 'cat /proc/meminfo | head -5'"
adb shell "su 0 sh -c 'cat /proc/swaps'"
adb shell "su 0 sh -c 'cat /proc/loadavg'"

# Run boot script manually
adb shell "su 0 sh -c '/data/local/linux/boot-auto.sh'"

# Boot log
adb shell "su 0 sh -c 'cat /data/local/linux/boot.log'"
```

### Emergency Recovery
```bash
# Supervisord socket missing:
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /bin/bash -c \"
  export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export HOME=/root
  mkdir -p /var/log/supervisor /var/log/server
  /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
\"'"

# Chroot not mounted:
adb shell "su 0 sh -c '/data/local/linux/boot-auto.sh'"

# WiFi dropped:
adb shell "su 0 sh -c 'svc wifi disable; sleep 2; svc wifi enable'"
# wait 15s, then:
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/tailscale up --ssh --accept-routes'"
```

---

## Real Numbers: Performance

I've been running this setup for months serving [Jaika](https://github.com/goyaljai/jaika) — a full AI voice assistant with LangChain pipelines, Gemini LLM, real-time STT/TTS, and image/video generation.

| Metric | Value |
|---|---|
| **CPU** | Snapdragon 778G (8-core ARM64) |
| **RAM** | 8GB physical + 8GB swap = 16GB effective |
| **Stack** | Flask + Gunicorn + LangChain + Gemini |
| **Concurrent users** | ~10–15 comfortable |
| **API response time** | ~200–500ms |
| **Tailscale overhead** | ~50–100ms added latency |
| **Memory footprint** | ~1.5GB (app + Python + system) |
| **Uptime** | Weeks (limited by WiFi stability) |
| **Monthly cost** | $0 |

The bottleneck is always WiFi. A phone plugged into power with the wake lock and sleep policy settings stays connected for weeks. When connectivity does drop, Tailscale reconnects automatically.

---

## Lessons Learned (The Hard Way)

1. **Never bind-mount `/dev`** from Android into the chroot. Use `tmpfs + mknod`. I learned this after a soft-brick.

2. **`setsid` is mandatory** when starting services from the boot script. Without it, Android's init can kill your chroot processes during cleanup.

3. **`pip` breaks inside the chroot** if you don't unset Android env vars first. The error messages are cryptic.

4. **`supervisorctl restart` doesn't reload env vars.** You need `reread` + `update`. Cost me 2 hours.

5. **The wake lock is essential.** Without `echo android-server > /sys/power/wake_lock`, the phone enters deep sleep and your server goes dark.

6. **GPU acceleration isn't available** in the chroot. LangChain and Gemini run CPU-only. For most API-heavy workloads this is fine — for on-device ML inference, you'd need NNAPI outside the chroot.

---

## When This Makes Sense (And When It Doesn't)

**Use a phone VPS for:**
- Side projects and personal tools
- AI chatbots and voice assistants with LangChain/Gemini
- Home automation servers
- Development and staging environments
- Demos and proof-of-concepts

**Don't use it for:**
- Services with strict uptime SLAs (WiFi will occasionally drop)
- High-bandwidth workloads (phone WiFi caps ~100Mbps)
- GPU-accelerated on-device ML inference
- Databases with heavy write loads (flash storage has limited write cycles)

---

## The Bigger Picture

We've been conditioned to think servers need to live in data centers. They don't. A server is just a computer that responds to requests. Your phone is a computer. A powerful one.

The convergence of ARM64 software support, tools like Tailscale, and Android's Linux kernel means the gap between "phone" and "server" has never been thinner. I'm not saying everyone should cancel their cloud accounts. But for the thousands of developers paying $50–100/month to host a side project that gets 10 requests an hour — check your drawer first.

Two phones. Zero dollars. LangChain, Gemini, real-time voice, multi-model fallback — all of it running on hardware you already own.

It just works.

---

*I'm [Jai Goyal](https://linkedin.com/in/goyaljai), an Android / AI engineer at Glance (InMobi Group). I write about Android, AI infrastructure, and questionable server decisions on [Medium](https://goyaljai.medium.com).*

*The app running on these phone servers is [Jaika](https://github.com/goyaljai/jaika) — an open-source AI assistant with real-time voice, LangChain pipelines, Gemini LLM, ElevenLabs TTS, and image/video generation. The only native AI product you want. Star it if this article saved you a server bill.*
