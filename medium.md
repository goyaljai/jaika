# I Threw Away My AWS Bill and Replaced It With Two Old Phones

*How I turned a pair of forgotten Android phones into production servers running an AI voice chatbot — for $0/month.*

---

Last year, I got my AWS bill and stared at it for a while. $73/month for a single EC2 instance. 8GB RAM. A t3.large. It ran my side project — an AI chatbot called [Jaika](https://github.com/goyaljai/jaika-v2) — and honestly, most of the time it was sitting idle, burning money.

Then I looked at my drawer. Two old Android phones. A Motorola with a Snapdragon 778G and 8GB RAM. A Samsung with similar specs. Both collecting dust.

Here's the thing nobody talks about: **your old Android phone is a more capable computer than the server most startups deploy on.** An 8-core ARM64 CPU, 8GB RAM, built-in WiFi, a battery that acts as a free UPS — and it fits in your pocket. The only reason we don't use phones as servers is that nobody told us we could.

So I did. Both phones now serve real users. Tailscale gives them public URLs. Supervisord keeps the processes alive. A boot script makes them self-healing. And my AWS bill? **$0.**

Here's exactly how I did it, command by command.

---

## What We're Building

By the end of this article, you'll have:

- A full **Ubuntu Linux environment** running inside your Android phone (no custom ROM needed — Android keeps working normally)
- **Supervisord** managing your app processes (auto-restart on crash)
- **Tailscale** giving your phone a public HTTPS URL accessible from anywhere
- **SSH access** to your phone from any device
- A **boot script** that brings everything back up automatically after a reboot
- A **deployment pipeline** that pushes code from your laptop to your phone in seconds

The architecture looks like this:

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
 Your App
```

Android runs normally on top. Your Linux server lives inside a chroot — a sandboxed directory that looks and feels like a real Ubuntu machine. They coexist peacefully.

---

## What You'll Need

Before we start, gather these:

| Item | Why |
|---|---|
| An **old Android phone** (4GB+ RAM) | This becomes your server |
| **Root access** on the phone | We need to create chroot mounts |
| A **USB cable** | For ADB commands during setup |
| A computer with **ADB installed** | Your control plane |
| **WiFi network** | Your phone needs internet |
| ~30 minutes | That's genuinely all it takes |

Don't have root? For most phones: unlock the bootloader with `fastboot oem unlock`, flash [Magisk](https://github.com/topjohnwu/Magisk) via recovery, and you're done. There are device-specific guides everywhere.

Verify root works:

```bash
adb shell "su 0 id"
# Should output: uid=0(root) gid=0(root)
```

If you see `uid=0(root)`, you're good. Let's go.

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

That's it. You now have Ubuntu's entire filesystem sitting at `/data/local/linux/rootfs/` on your phone. But it can't do anything yet — it needs to be "wired up" to the kernel.

---

## Step 2: Wire Up the Chroot

A chroot is like a room with no windows. The programs inside can see the files in `/data/local/linux/rootfs/`, but they can't see the rest of the phone. We need to give them access to a few critical kernel interfaces:

- **`/proc`** — process information (every Linux program needs this)
- **`/sys`** — hardware/kernel info
- **`/dev`** — device nodes (null, random, tty — the basics)
- **DNS** — so `apt` and `curl` can resolve hostnames

Here's the mount script:

```bash
adb shell "su 0 sh -c '
  ROOTFS=/data/local/linux/rootfs

  # ── /dev — THIS IS THE TRICKY PART ──
  # DO NOT bind-mount Android /dev into the chroot.
  # Android /dev has binder nodes, hwbinder, vndbinder — if you expose
  # these inside the chroot, you will break ADB, crash system_server,
  # and possibly soft-brick your phone.
  #
  # Instead: create a clean tmpfs and only make the device nodes you need.
  mkdir -p $ROOTFS/dev $ROOTFS/dev/pts $ROOTFS/dev/shm
  mount -t tmpfs tmpfs $ROOTFS/dev
  mkdir -p $ROOTFS/dev/pts $ROOTFS/dev/shm

  # The seven essential device nodes
  mknod -m 666 $ROOTFS/dev/null    c 1 3   # black hole
  mknod -m 666 $ROOTFS/dev/zero    c 1 5   # infinite zeros
  mknod -m 666 $ROOTFS/dev/full    c 1 7   # always-full device
  mknod -m 444 $ROOTFS/dev/random  c 1 8   # random bytes (blocking)
  mknod -m 444 $ROOTFS/dev/urandom c 1 9   # random bytes (non-blocking)
  mknod -m 666 $ROOTFS/dev/tty     c 5 0   # current terminal
  mknod -m 620 $ROOTFS/dev/ptmx    c 5 2   # PTY master (for SSH)

  # Standard symlinks every Linux system expects
  ln -sf /proc/self/fd   $ROOTFS/dev/fd
  ln -sf /proc/self/fd/0 $ROOTFS/dev/stdin
  ln -sf /proc/self/fd/1 $ROOTFS/dev/stdout
  ln -sf /proc/self/fd/2 $ROOTFS/dev/stderr

  # ── Kernel filesystems ──
  mount -o bind /dev/pts $ROOTFS/dev/pts    # PTY slave devices (SSH needs this)
  mount -t tmpfs tmpfs $ROOTFS/dev/shm      # POSIX shared memory
  mount -t proc  proc  $ROOTFS/proc         # process info
  mount -t sysfs sysfs $ROOTFS/sys          # hardware/kernel info
  mount -t tmpfs tmpfs $ROOTFS/tmp          # scratch space
  mount -t tmpfs tmpfs $ROOTFS/run          # runtime state (PID files, sockets)

  # ── DNS ──
  # Android manages DNS dynamically. Our chroot needs static resolvers.
  echo "nameserver 8.8.8.8" >  $ROOTFS/etc/resolv.conf
  echo "nameserver 1.1.1.1" >> $ROOTFS/etc/resolv.conf
'"
```

**Why `tmpfs + mknod` instead of `mount -o bind /dev`?** I learned this the hard way. The first time I tried bind-mounting `/dev`, my phone's screen froze, ADB disconnected, and I had to force-reboot. Android's `/dev` is a minefield of binder nodes that the Android runtime depends on. Exposing them inside the chroot causes cross-namespace conflicts. The `tmpfs + mknod` approach gives you a clean `/dev` with only the 7 device nodes Linux actually needs. Safe, predictable, works across every phone I've tested.

---

## Step 3: Hello, Ubuntu

Let's enter our chroot for the first time and install the essentials:

```bash
adb shell "su 0 sh -c '
  chroot /data/local/linux/rootfs /bin/bash -c \"
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export HOME=/root

    # First things first
    apt update
    apt install -y python3 python3-pip python3-venv openssh-server \
                   supervisor curl wget git
  \"
'"
```

You're now running `apt install` on a phone. Let that sink in for a moment.

From here, you can install anything that runs on ARM64 Linux. Python, Node.js, Go, Rust, PostgreSQL, Redis — if it compiles for `aarch64`, it runs here.

**Important gotcha for `pip`:** Android sets environment variables (`ANDROID_ROOT`, `ANDROID_DATA`, etc.) that confuse pip inside the chroot. Always unset them first:

```bash
unset ANDROID_ROOT ANDROID_DATA ANDROID_ASSETS ANDROID_STORAGE
pip install flask gunicorn requests
```

---

## Step 4: Tailscale — Give Your Phone a Public URL

Your phone is on WiFi behind a router. It has a private IP like `192.168.0.15`. Nobody outside your network can reach it. You could forward ports on your router, but that's fragile, insecure, and breaks when your IP changes.

[Tailscale](https://tailscale.com) solves this elegantly. It creates a WireGuard VPN mesh between your devices and gives each one a stable DNS name. Your phone becomes `https://my-phone.tailnet-name.ts.net` — accessible from anywhere, encrypted, no port forwarding.

```bash
# Inside the chroot
curl -fsSL https://tailscale.com/install.sh | sh
```

Now start the daemon. One critical flag: `--tun=userspace-networking`. Android doesn't expose `/dev/net/tun` to the chroot (it's used by Android's own VPN subsystem), so Tailscale needs to run in userspace networking mode:

```bash
# Start the daemon
tailscaled --state=/var/lib/tailscale/tailscaled.state \
           --tun=userspace-networking &

# Wait a few seconds for it to initialize
sleep 5

# Authenticate — this prints a URL, open it in your browser
tailscale up --ssh --accept-routes
```

The `--ssh` flag enables Tailscale SSH, which means you can SSH into your phone from any device on your tailnet without managing SSH keys. The `--accept-routes` flag lets you route traffic through other nodes if needed.

After authenticating, verify:

```bash
tailscale status
# You should see your phone listed with a 100.x.x.x IP
```

Your phone is now on the internet. Try hitting `https://your-phone.tailnet-name.ts.net` from your laptop's browser. You'll get a connection refused (no web server yet), but the DNS resolves — that's the magic.

---

## Step 5: Supervisord — Keep Everything Alive

Processes crash. WiFi reconnects. Phones restart. You need something that watches your services and restarts them automatically. That's Supervisord.

Create your app config at `/etc/supervisor/conf.d/myapp.conf`:

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

And one for Tailscale at `/etc/supervisor/conf.d/tailscaled.conf`:

```ini
[program:tailscaled]
command=/usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --tun=userspace-networking
autostart=true
autorestart=true
startsecs=5
```

Start Supervisord:

```bash
mkdir -p /var/log/supervisor /var/log/server
supervisord -c /etc/supervisor/supervisord.conf
```

Check everything's running:

```bash
supervisorctl status
# myapp        RUNNING   pid 1234, uptime 0:00:05
# tailscaled   RUNNING   pid 1235, uptime 0:00:05
```

**Pro tip:** `supervisorctl restart myapp` restarts the process but does NOT re-read environment variables from the config file. If you change env vars, you need:

```bash
supervisorctl reread   # re-parse config files
supervisorctl update   # apply changes (restarts affected processes)
```

This bit me for hours. Don't let it bite you.

---

## Step 6: The Boot Script — Self-Healing on Reboot

Everything works now, but if the phone reboots (power outage, Android update, accidental restart), all your mounts and processes are gone. You need a script that brings everything back automatically.

Save this as `/data/local/linux/boot-auto.sh` on the phone (outside the chroot — this runs in Android's namespace):

```bash
#!/system/bin/sh
# PocketServer — Boot Script
# Runs on every boot, brings up the full Linux server stack.

LOG=/data/local/linux/boot.log
ROOTFS=/data/local/linux/rootfs
SWAPFILE=/data/local/swapfile

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> $LOG; }
log "=== BOOT SERVICE STARTING ==="

# ── Wait for Android to finish booting ──
# Android takes 30-60s to fully boot. If we start too early,
# system services like WiFi aren't ready yet.
while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 2; done
sleep 10
log "System boot completed"

# ── Performance Tweaks ──
# These prevent Android from killing our server processes or
# putting the phone to sleep.

# Disable SELinux enforcement (chroot needs this)
setenforce 0 2>/dev/null

# Keep device awake when plugged in (USB/AC/wireless = bitmask 7)
settings put global stay_on_while_plugged_in 7

# Never turn off WiFi during sleep
settings put global wifi_sleep_policy 2

# Prevent Android from killing background processes
settings put global app_standby_enabled 0
settings put global adaptive_battery_management_enabled 0

# Acquire a wake lock (prevents deep sleep)
echo android-server > /sys/power/wake_lock 2>/dev/null

log "System tweaks applied"

# ── WiFi ──
# Enable WiFi and wait for it to connect to a saved network.
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
log "WiFi status: $(ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && echo connected || echo offline)"

# ── Swap ──
# Create an 8GB swap file for extra memory.
# 8GB phone + 8GB swap = 16GB effective RAM.
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
# (Same mounts from Step 2 — tmpfs /dev, mknod, proc, sys, etc.)
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

# DNS
echo "nameserver 8.8.8.8" >  $ROOTFS/etc/resolv.conf
echo "nameserver 1.1.1.1" >> $ROOTFS/etc/resolv.conf

touch /data/local/linux/.mounted
log "Chroot mounted"

# ── Start All Services ──
# setsid detaches the process from init, so Android can't kill it
# during its cleanup phase.
setsid chroot $ROOTFS /bin/bash -c "
  export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export HOME=/root

  mkdir -p /run/sshd /var/log/supervisor /var/log/server
  /usr/sbin/sshd 2>/dev/null
  /usr/sbin/cron 2>/dev/null
  /usr/bin/supervisord -c /etc/supervisor/supervisord.conf 2>/dev/null

  # Give supervisord time to start tailscaled, then connect
  sleep 8
  tailscale up --ssh --accept-routes &
" < /dev/null > /dev/null 2>&1 &

sleep 15
log "All services started"
log "=== BOOT SERVICE COMPLETE ==="
```

To make this run on every boot, create a Magisk boot service or add it to a custom `init.d` script. With Magisk, place a trigger script in `/data/adb/service.d/`:

```bash
# /data/adb/service.d/boot-server.sh
#!/system/bin/sh
/data/local/linux/boot-auto.sh &
```

Make it executable: `chmod 755 /data/adb/service.d/boot-server.sh`

Now reboot your phone. Watch the boot log:

```bash
adb shell "su 0 sh -c 'tail -f /data/local/linux/boot.log'"
```

You should see it march through each step: boot wait, tweaks, WiFi, swap, mount, services. Within 2-3 minutes of a cold boot, your server is back online.

---

## Step 7: Deploy Your App

You've built an app on your laptop. Getting it onto the phone takes three commands:

```bash
# 1. Package your app (exclude venv and cache — they'll be on the phone)
tar -czf /tmp/deploy.tar.gz \
  --exclude="__pycache__" \
  --exclude=".venv" \
  --exclude="*.pyc" \
  --exclude=".env" \
  -C /path/to/your/project .

# 2. Push to the phone
adb push /tmp/deploy.tar.gz /data/local/tmp/

# 3. Extract into chroot and restart
adb shell "su 0 sh -c '
  tar -xzf /data/local/tmp/deploy.tar.gz -C /data/local/linux/rootfs/opt/myapp/
  rm /data/local/tmp/deploy.tar.gz
  chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart myapp
'"
```

**Deploying to multiple phones?** I wrote a `push_devices.sh` script that does this in parallel across both my devices:

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

Entire deploy takes under 5 seconds per device.

---

## The Command Cheat Sheet

Bookmark this. You'll use these daily.

### Service Management
```bash
# Check all services
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl status'"

# Restart your app
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart myapp'"

# Restart with new env vars (after editing supervisor .conf)
adb shell "su 0 sh -c '
  chroot /data/local/linux/rootfs /usr/bin/supervisorctl reread
  chroot /data/local/linux/rootfs /usr/bin/supervisorctl update
'"

# View app logs
adb shell "su 0 sh -c 'tail -50 /data/local/linux/rootfs/var/log/server/myapp-err.log'"
```

### Tailscale / Networking
```bash
# Check Tailscale status
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/tailscale status'"

# Reconnect Tailscale (after WiFi drop)
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart tailscaled'"
sleep 8
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/tailscale up --ssh --accept-routes'"

# Check if phone has internet
adb shell "su 0 sh -c 'ping -c1 8.8.8.8 && echo ONLINE || echo OFFLINE'"
```

### System / Debugging
```bash
# Get a shell inside the chroot
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /bin/bash'"

# Check RAM usage
adb shell "su 0 sh -c 'cat /proc/meminfo | head -5'"

# Check swap usage
adb shell "su 0 sh -c 'cat /proc/swaps'"

# Check CPU load
adb shell "su 0 sh -c 'cat /proc/loadavg'"

# Run boot script manually (if auto-boot didn't trigger)
adb shell "su 0 sh -c '/data/local/linux/boot-auto.sh'"

# Check boot log
adb shell "su 0 sh -c 'cat /data/local/linux/boot.log'"

# Reboot the phone
adb reboot
```

### Emergency Recovery
```bash
# Phone frozen, ADB unresponsive:
# → Hold Power + Volume Down for 15 seconds (hardware force reboot)
# → boot-auto.sh will bring everything back up automatically

# Supervisord socket missing (unix:///var/run/supervisor.sock no such file):
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /bin/bash -c \"
  export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export HOME=/root
  mkdir -p /var/log/supervisor /var/log/server
  /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
\"'"

# Chroot not mounted (services fail with 'command not found'):
adb shell "su 0 sh -c '/data/local/linux/boot-auto.sh'"

# WiFi dropped, Tailscale offline:
adb shell "su 0 sh -c 'svc wifi disable; sleep 2; svc wifi enable'"
# Wait 15s for reconnect, then:
adb shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/tailscale up --ssh --accept-routes'"
```

---

## Real Numbers: Performance

I've been running this setup for months. Here's what a phone can actually handle:

| Metric | Value |
|---|---|
| **CPU** | Snapdragon 778G (8-core ARM64) |
| **RAM** | 8GB physical + 8GB swap = 16GB effective |
| **App** | Flask + Gunicorn (4 workers x 4 threads) |
| **Concurrent users** | ~10-15 comfortable |
| **Response time** | ~200-500ms for API calls |
| **Tailscale overhead** | ~50-100ms added latency |
| **Memory footprint** | ~1.5GB (app + Python + system) |
| **Uptime** | Weeks (limited by WiFi stability) |
| **Monthly cost** | $0 (electricity is negligible) |

The bottleneck is always WiFi. A phone plugged into power with `stay_on_while_plugged_in=7` and `wifi_sleep_policy=2` stays connected for weeks. But eventually, your router will restart, or your ISP will hiccup, and the phone loses connectivity for a few minutes. Tailscale reconnects automatically. If it doesn't, the boot script has a WiFi retry loop.

For my use case — an AI chatbot that gets a few hundred requests per day — two phones with load balancing is more than enough.

---

## Lessons Learned (The Hard Way)

1. **Never bind-mount `/dev`** from Android into the chroot. Use `tmpfs + mknod`. I learned this after a soft-brick that required a factory reset.

2. **`setsid` is mandatory** when starting services from the boot script. Without it, Android's init process can kill your chroot processes during its cleanup phase.

3. **`pip` breaks inside chroot** if you don't unset Android env vars (`ANDROID_ROOT`, `ANDROID_DATA`, etc.). The error messages are cryptic and unhelpful.

4. **`supervisorctl restart` doesn't reload env vars.** You need `reread` + `update`. I spent 2 hours debugging "why did my API key not change" before figuring this out.

5. **Phone deep sleep kills everything.** The wake lock (`echo android-server > /sys/power/wake_lock`) is essential. Without it, the phone enters deep sleep after a few minutes of screen-off, and your server goes dark.

6. **GPU acceleration isn't available** in the chroot. If you need ML inference on the phone, you'd need to use Android's NNAPI or TFLite outside the chroot. Inside the chroot, you're CPU-only.

---

## When This Makes Sense (And When It Doesn't)

**Use a phone VPS for:**
- Side projects and personal tools
- AI chatbots and voice assistants
- Home automation servers
- Development/staging environments
- Demos and proof-of-concepts
- Learning Linux server administration

**Don't use a phone VPS for:**
- Services with uptime SLAs (WiFi will drop)
- High-bandwidth workloads (phone WiFi maxes ~100Mbps)
- GPU-accelerated ML inference
- Databases with heavy write loads (flash storage has limited write cycles)
- Anything where a 2-minute reboot window is unacceptable

---

## The Bigger Picture

We've been conditioned to think servers need to be in data centers. They don't. A server is just a computer that responds to requests. Your phone is a computer. A powerful one.

The convergence of ARM64 software support, tools like Tailscale, and Android's Linux kernel means the barrier between "phone" and "server" is thinner than ever. I'm not saying everyone should throw away their cloud instances. But for the thousands of developers paying $50-100/month to host a side project that gets 10 requests per hour — maybe look at your drawer first.

Two phones. Zero dollars. It just works.

---

*I'm [Jai Goyal](https://linkedin.com/in/goyaljai), an Android engineer who apparently can't stop turning phones into things they weren't meant to be. I write about Android, AI, and the occasional questionable infrastructure decision on [Medium](https://goyaljai.medium.com).*

*The project running on these phone servers is [Jaika](https://github.com/goyaljai/jaika-v2) — an open-source AI chatbot with real-time voice (ElevenLabs TTS, Gemini STT, VAD auto-stop, filler audio for perceived latency). Star it if you're into that sort of thing.*
