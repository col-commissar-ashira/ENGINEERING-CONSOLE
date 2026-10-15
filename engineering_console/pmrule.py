"""The exact powermetrics command line Engineering Console uses, shared by the app and the one-time setup script.
Stdlib only on purpose (the setup script runs it with the system Python)."""
import platform

POWERMETRICS = "/usr/bin/powermetrics"


def args(arm=None):
    arm = (platform.machine() == "arm64") if arm is None else arm
    samplers = "cpu_power,gpu_power,ane_power,thermal" if arm else "cpu_power,gpu_power,smc,thermal"
    return ["-i", "1000", "--samplers", samplers, "-f", "text", "-n", "30"]      # 30 one-second samples per run


def sudoers_rule(user, arm=None):
    """One sudoers line that lets `user` run powermetrics with EXACTLY these arguments and nothing else, so it cannot
    be used to write files (-o) or run anything different as root. Commas must be escaped in sudoers."""
    a = " ".join(x.replace(",", "\\,") for x in args(arm))
    return f"{user} ALL=(root) NOPASSWD: {POWERMETRICS} {a}"
