# Device Compatibility & Network Reference — Narwal Local Skill

This document details hardware model support, public protocol product keys, and network topology requirements for `narwal-local`.

---

## 1. Compatibility Matrix

Narwal Local Skill communicates exclusively via the local WebSocket protocol on **TCP port 9002**.

| Model | Hardware Status | Upstream ID | Protocol Stack | Notes |
|---|---|---|---|---|
| **Narwal Flow 2** | **Hardware Validated** | `QxMSPG6VSO`, `iSuVlI1If2`, `mkbqaprvrb` | WS 9002 | Direct physical hardware smoke testing verified on firmware `v01.09.10.02`. |
| **Narwal Flow (AX12)** | Candidate | `QoEsI5qYXO` | WS 9002 | Inherits protocol compatibility from upstream community research. |
| **Freo Z10 Ultra (CX4)** | Candidate | `DrzDKQ0MU8` | WS 9002 | Upstream community reported; candidate support. |
| **Freo Z10 Pro / Turbo (AX26)** | Candidate | `qV6BujoYLz` | WS 9002 | Upstream community reported; candidate support. |
| **Freo X10 Pro (AX15)** | Candidate | `CNbforyZWI` | WS 9002 | Upstream community reported; candidate support. |
| **Freo 20** | Candidate | `fjhpiem4ba` | WS 9002 | Upstream community reported; candidate support. |
| **Freo 20 Edge** | Candidate | `ulonq49mm1` | WS 9002 | Upstream community reported; candidate support. |
| **Narwal JX** | Candidate | `CGjuB6dzq7` | WS 9002 | Upstream community reported; candidate support. |
| **Freo Z Ultra (CX7 / J5)** | Candidate (Limited) | `hEA7OEshlx` | WS 9002 | Upstream-tested variant needs explicit `--device-id`; no live broadcast position/progress. `BYWBPqSxeC` is recognized but its variant remains unresolved. |
| **Freo Z10 (standard)** | **Incompatible** | — | — | Port 9002 refused (`ECONNREFUSED`); under upstream investigation. |
| **Freo X Ultra (AX18/AX19)** | **Incompatible** | — | ZeroMQ 6789 | Uses ZeroMQ on TCP port 6789 and Tuya cloud; no WebSocket on port 9002. |
| **Freo X Plus** | **Incompatible** | — | Cloud-only | No local port 9002 WebSocket interface available. |
| **Narwal J1 / J4 / T10** | **Incompatible** | — | Cloud-only | Legacy models use cloud API or cloud-mediated WebSockets. |

> [!IMPORTANT] Hardware Validation Boundary
> Only the **Narwal Flow 2** has undergone direct physical hardware verification. All other models listed as "Candidate" inherit compatibility from upstream community reverse-engineering (`sjmotew/NarwalIntegration`).

---

## 2. Public Model Product Keys

These public model-level product keys are protocol constants used in topic addressing (e.g., `/<product_key>/<device_id>/<topic>`). They represent model classes and are **not** private per-device serial numbers:

```python
WS9002_MODELS = {
    "QoEsI5qYXO": "Narwal Flow",
    "QxMSPG6VSO": "Narwal Flow 2",
    "iSuVlI1If2": "Narwal Flow 2",
    "mkbqaprvrb": "Narwal Flow 2",
    "DrzDKQ0MU8": "Freo Z10 Ultra",
    "qV6BujoYLz": "Freo Z10 Pro/Turbo",
    "hEA7OEshlx": "Freo Z Ultra",
    "fjhpiem4ba": "Freo 20",
    "ulonq49mm1": "Freo 20 Edge",
    "BYWBPqSxeC": "Freo Z Ultra",
    "CGjuB6dzq7": "Narwal JX",
    "CNbforyZWI": "Freo X10 Pro",
}
```

If an operator supplies an explicit product key not present in this dictionary, the CLI accepts it with `device.model: null`.

---

## 3. Network Topology & Routing Requirements

### Local Subnet (Recommended)
Place the workstation or agent host on the same Wi-Fi subnet / VLAN as the robot vacuum. Ensure TCP port 9002 is reachable without stateful firewall blocks.

### Cross-VLAN Routing & Router SNAT
Upstream reports some firmware/setups not answering connections from another subnet. This project has not tested cross-VLAN routing and cannot treat that behavior as universal.

If routing and firewall checks pass but cross-subnet queries still fail, source NAT is an option to evaluate with the network administrator:
- Translate the CLI host's source IP address to the router's IP address on the robot's local subnet.
- The robot sees the connection arriving from its default gateway on the local subnet and accepts the TCP connection.

### Single Connection per Source IP
The vacuum permits only one active WebSocket connection per source IP address. If a second connection is initiated from the same IP, the robot closes the earlier connection with the message:
`"connection with same ip, close old one"`

`narwal-local` preserves this diagnostic detail in the error payload when it occurs. Avoid running concurrent CLI commands against the same robot from the same host machine.
