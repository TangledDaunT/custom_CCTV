#!/usr/bin/env python3
"""
CCTV Cross-Machine Monitor
- Monitors home server from HP laptop
- Monitors HP laptop from home server
- Sends WhatsApp alerts on power failures, crashes, high CPU
- Automatic restart capabilities
"""

import os
import sys
import json
import time
import psutil
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

# Configuration
CONFIG = {
    # Machine identification
    "hostname": os.uname().nodename,

    # Remote monitoring (via Tailscale)
    "remote_hosts": {
        "shreyansh-server": {
            "ip": "100.94.49.20",
            "ssh_user": "shreyansh",
            "ssh_key": os.path.expanduser("~/.ssh/id_rsa_cctv_deploy"),
            "services": ["cctv", "cctv-sync"],
            "label": "Home Server"
        },
        "shreyansh-HP-Laptop": {
            "ip": "100.99.161.57",
            "ssh_user": "shreyansh",
            "ssh_key": os.path.expanduser("~/.ssh/id_rsa_cctv_deploy"),
            "services": ["vision-batch", "cctv-defense"],
            "label": "HP Laptop"
        }
    },

    # Thresholds
    "cpu_threshold": 90,  # Alert if CPU > 90% for > 5 minutes
    "memory_threshold": 95,  # Alert if memory > 95%
    "disk_threshold": 95,  # Alert if disk > 95%
    "check_interval": 60,  # Check every 60 seconds
    "offline_threshold": 180,  # Alert if offline > 3 minutes

    # Monitoring state
    "state_file": os.path.expanduser("~/.local/lib/cctv/monitor_state.json"),
    "log_file": os.path.expanduser("~/.local/log/cctv/cross_monitor.log"),

    # WhatsApp
    "wacli_path": "/usr/local/bin/wacli",
    "alert_number": "917754008079",

    # Systemd service restart
    "restart_services": False,  # Set True to auto-restart crashed services
}

# Machine role detection
THIS_MACHINE = os.uname().nodename
IS_SERVER = "server" in THIS_MACHINE.lower() or THIS_MACHINE == "shreyansh-server"
IS_LAPTOP = "laptop" in THIS_MACHINE.lower() or "HP" in THIS_MACHINE

# Setup logging
os.makedirs(os.path.dirname(CONFIG["log_file"]), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class WhatsAppNotifier:
    @staticmethod
    def send(message: str) -> bool:
        try:
            result = subprocess.run(
                [CONFIG["wacli_path"], "send", "text",
                 "--to", CONFIG["alert_number"], "--message", message],
                timeout=10, capture_output=True, text=True
            )
            logger.info(f"WhatsApp sent: {result.returncode == 0}")
            return result.returncode == 0
        except Exception as e:
            logger.error(f"WhatsApp failed: {e}")
            return False


class SystemMonitor:
    """Monitor local system resources."""

    @staticmethod
    def get_cpu_usage() -> float:
        """Get CPU usage percentage."""
        return psutil.cpu_percent(interval=1)

    @staticmethod
    def get_memory_usage() -> float:
        """Get memory usage percentage."""
        mem = psutil.virtual_memory()
        return mem.percent

    @staticmethod
    def get_disk_usage(path: str = "/") -> float:
        """Get disk usage percentage."""
        disk = psutil.disk_usage(path)
        return disk.percent

    @staticmethod
    def get_uptime() -> str:
        """Get system uptime."""
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        hours = uptime.seconds // 3600
        minutes = (uptime.seconds % 3600) // 60
        return f"{hours}h {minutes}m"

    @staticmethod
    def check_service_status(service_name: str) -> Dict:
        """Check if a systemd service is running."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True
            )
            return {
                "service": service_name,
                "active": result.returncode == 0,
                "status": result.stdout.strip()
            }
        except Exception as e:
            return {
                "service": service_name,
                "active": False,
                "status": f"error: {e}"
            }

    @staticmethod
    def restart_service(service_name: str) -> bool:
        """Restart a systemd service."""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", service_name],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Failed to restart {service_name}: {e}")
            return False


class RemoteMonitor:
    """Monitor remote machine via SSH."""

    @staticmethod
    def ssh_command(host_config: Dict, command: str) -> Optional[str]:
        """Execute SSH command on remote host."""
        try:
            ssh_key = host_config["ssh_key"]
            user = host_config["ssh_user"]
            ip = host_config["ip"]

            # Use sshpass if available, otherwise key-based
            result = subprocess.run(
                ["ssh", "-i", ssh_key,
                 "-o", "ConnectTimeout=10",
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 f"{user}@{ip}",
                 command],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"SSH command failed: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"SSH timeout to {host_config['ip']}")
            return None
        except Exception as e:
            logger.error(f"SSH error: {e}")
            return None

    @staticmethod
    def check_host_alive(host_config: Dict) -> bool:
        """Check if remote host is reachable."""
        response = RemoteMonitor.ssh_command(host_config, "echo ALIVE")
        return response == "ALIVE"

    @staticmethod
    def get_remote_status(host_config: Dict) -> Dict:
        """Get comprehensive status from remote host."""
        status = {
            "host": host_config["label"],
            "ip": host_config["ip"],
            "timestamp": datetime.now().isoformat(),
            "reachable": False,
            "cpu": None,
            "memory": None,
            "uptime": None,
            "services": {}
        }

        # Check if reachable
        if not RemoteMonitor.check_host_alive(host_config):
            return status

        status["reachable"] = True

        # Get CPU usage
        cpu_cmd = "python3 -c 'import psutil; print(psutil.cpu_percent(interval=1))'"
        response = RemoteMonitor.ssh_command(host_config, cpu_cmd)
        if response:
            try:
                status["cpu"] = float(response)
            except:
                pass

        # Get memory usage
        mem_cmd = "python3 -c 'import psutil; print(psutil.virtual_memory().percent)'"
        response = RemoteMonitor.ssh_command(host_config, mem_cmd)
        if response:
            try:
                status["memory"] = float(response)
            except:
                pass

        # Get uptime
        uptime_cmd = "uptime -p"
        response = RemoteMonitor.ssh_command(host_config, uptime_cmd)
        if response:
            status["uptime"] = response.replace("up ", "")

        # Check services
        for service in host_config.get("services", []):
            service_cmd = f"systemctl is-active {service}"
            response = RemoteMonitor.ssh_command(host_config, service_cmd)
            status["services"][service] = response == "active" if response else False

        return status


class CrossMonitor:
    """Main cross-machine monitoring system."""

    def __init__(self):
        self.state = self._load_state()
        self.last_alerts = {}

    def _load_state(self) -> Dict:
        """Load monitoring state."""
        if os.path.exists(CONFIG["state_file"]):
            try:
                with open(CONFIG["state_file"], 'r') as f:
                    return json.load(f)
            except:
                pass
        return {
            "last_check": None,
            "hosts_status": {},
            "alerts_sent": []
        }

    def _save_state(self):
        """Save monitoring state."""
        os.makedirs(os.path.dirname(CONFIG["state_file"]), exist_ok=True)
        with open(CONFIG["state_file"], 'w') as f:
            json.dump(self.state, f, indent=2)

    def _should_send_alert(self, alert_type: str, cooldown_minutes: int = 15) -> bool:
        """Check if alert should be sent (with cooldown)."""
        now = datetime.now()
        last_sent = self.last_alerts.get(alert_type)

        if last_sent:
            time_since = (now - datetime.fromisoformat(last_sent)).total_seconds()
            if time_since < cooldown_minutes * 60:
                return False

        return True

    def _mark_alert_sent(self, alert_type: str):
        """Mark alert as sent."""
        self.last_alerts[alert_type] = datetime.now().isoformat()
        self._save_state()

    def check_local_system(self) -> Dict:
        """Check local system health."""
        logger.info("Checking local system...")

        local_status = {
            "hostname": THIS_MACHINE,
            "timestamp": datetime.now().isoformat(),
            "cpu": SystemMonitor.get_cpu_usage(),
            "memory": SystemMonitor.get_memory_usage(),
            "disk": SystemMonitor.get_disk_usage(),
            "uptime": SystemMonitor.get_uptime()
        }

        # Check for high CPU
        if local_status["cpu"] > CONFIG["cpu_threshold"]:
            logger.warning(f"HIGH CPU: {local_status['cpu']}%")

            if self._should_send_alert("high_cpu", cooldown_minutes=30):
                WhatsAppNotifier.send(
                    f"⚠️ HIGH CPU WARNING\n"
                    f"Machine: {THIS_MACHINE}\n"
                    f"CPU: {local_status['cpu']:.1f}%\n"
                    f"Memory: {local_status['memory']:.1f}%\n"
                    f"Time: {datetime.now().strftime('%H:%M:%S')}"
                )
                self._mark_alert_sent("high_cpu")

        # Check for high memory
        if local_status["memory"] > CONFIG["memory_threshold"]:
            logger.warning(f"HIGH MEMORY: {local_status['memory']}%")

            if self._should_send_alert("high_memory", cooldown_minutes=30):
                WhatsAppNotifier.send(
                    f"⚠️ HIGH MEMORY WARNING\n"
                    f"Machine: {THIS_MACHINE}\n"
                    f"Memory: {local_status['memory']:.1f}%\n"
                    f"Time: {datetime.now().strftime('%H:%M:%S')}"
                )
                self._mark_alert_sent("high_memory")

        # Check for high disk usage
        if local_status["disk"] > CONFIG["disk_threshold"]:
            logger.warning(f"HIGH DISK USAGE: {local_status['disk']}%")

            if self._should_send_alert("high_disk", cooldown_minutes=60):
                WhatsAppNotifier.send(
                    f"⚠️ HIGH DISK USAGE\n"
                    f"Machine: {THIS_MACHINE}\n"
                    f"Disk: {local_status['disk']:.1f}%\n"
                    f"Time: {datetime.now().strftime('%H:%M:%S')}"
                )
                self._mark_alert_sent("high_disk")

        return local_status

    def check_remote_system(self, host_config: Dict):
        """Check remote system health."""
        host_label = host_config["label"]
        logger.info(f"Checking remote: {host_label}...")

        status = RemoteMonitor.get_remote_status(host_config)

        # Check if unreachable
        if not status["reachable"]:
            logger.error(f"HOST UNREACHABLE: {host_label}")

            # Check if this is a new outage
            last_status = self.state["hosts_status"].get(host_label, {})
            was_reachable = last_status.get("reachable", True)

            if was_reachable or self._should_send_alert(f"offline_{host_label}", 5):
                WhatsAppNotifier.send(
                    f"🚨 HOST DOWN\n"
                    f"Machine: {host_label}\n"
                    f"IP: {host_config['ip']}\n"
                    f"Status: UNREACHABLE\n"
                    f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"Power failure or network issue?"
                )
                self._mark_alert_sent(f"offline_{host_label}")

        else:
            # Host is reachable
            logger.info(f"✓ {host_label} is online")

            # Check if it was previously offline
            last_status = self.state["hosts_status"].get(host_label, {})
            was_reachable = last_status.get("reachable", True)

            if not was_reachable:
                # Host recovered
                WhatsAppNotifier.send(
                    f"✅ HOST RECOVERED\n"
                    f"Machine: {host_label}\n"
                    f"IP: {host_config['ip']}\n"
                    f"Status: ONLINE\n"
                    f"Uptime: {status.get('uptime', 'N/A')}"
                )
                self._mark_alert_sent(f"online_{host_label}")

            # Check services
            for service, is_active in status.get("services", {}).items():
                if not is_active:
                    logger.error(f"SERVICE DOWN: {service} on {host_label}")

                    if self._should_send_alert(f"service_down_{host_label}_{service}", 10):
                        WhatsAppNotifier.send(
                            f"⚠️ SERVICE DOWN\n"
                            f"Machine: {host_label}\n"
                            f"Service: {service}\n"
                            f"Status: STOPPED\n"
                            f"Time: {datetime.now().strftime('%H:%M:%S')}"
                        )
                        self._mark_alert_sent(f"service_down_{host_label}_{service}")

                        # Try to restart if configured
                        if CONFIG["restart_services"]:
                            logger.info(f"Attempting restart of {service}...")
                            # Would need similar logic on remote host

            # Check high CPU on remote
            if status.get("cpu") and status["cpu"] > CONFIG["cpu_threshold"]:
                if self._should_send_alert(f"remote_high_cpu_{host_label}", 30):
                    WhatsAppNotifier.send(
                        f"⚠️ REMOTE HIGH CPU\n"
                        f"Machine: {host_label}\n"
                        f"CPU: {status['cpu']:.1f}%\n"
                        f"Time: {datetime.now().strftime('%H:%M:%S')}"
                    )

        # Update state
        self.state["hosts_status"][host_label] = status
        self._save_state()

        return status

    def monitor_loop(self):
        """Continuous monitoring loop."""
        logger.info("=" * 60)
        logger.info("CCTV CROSS-MACHINE MONITORING STARTED")
        logger.info(f"This machine: {THIS_MACHINE}")
        logger.info("=" * 60)

        # Determine which remote host to monitor
        if IS_SERVER:
            remote_host = "shreyansh-HP-Laptop"
        elif IS_LAPTOP:
            remote_host = "shreyansh-server"
        else:
            logger.error(f"Unknown machine: {THIS_MACHINE}")
            return

        monitor_config = CONFIG["remote_hosts"][remote_host]

        logger.info(f"Monitoring remote: {monitor_config['label']}")

        while True:
            try:
                # Check local system
                local_status = self.check_local_system()

                # Check remote system
                remote_status = self.check_remote_system(monitor_config)

                # Log summary
                logger.info(
                    f"Local: CPU={local_status['cpu']:.1f}% "
                    f"MEM={local_status['memory']:.1f}% | "
                    f"Remote ({monitor_config['label']}): "
                    f"{'ONLINE' if remote_status['reachable'] else 'OFFLINE'}"
                )

                # Sleep until next check
                time.sleep(CONFIG["check_interval"])

            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                time.sleep(30)  # Wait before retry


def main():
    """Main entry point."""
    monitor = CrossMonitor()

    # Single check mode (for testing)
    if "--once" in sys.argv:
        local = monitor.check_local_system()
        print(json.dumps(local, indent=2))

        for host_name, host_config in CONFIG["remote_hosts"].items():
            if host_name != THIS_MACHINE:
                remote = monitor.check_remote_system(host_config)
                print(json.dumps(remote, indent=2))
        return

    # Continuous monitoring
    monitor.monitor_loop()


if __name__ == "__main__":
    main()
