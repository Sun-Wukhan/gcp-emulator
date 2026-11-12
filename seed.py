#!/usr/bin/env python3
"""Seed the gcp-emulator with a small test project via its real insert routes."""
import sys

import requests

BASE = "http://127.0.0.1:8800"
PROJECT = "tf-test-project"
REGION = "us-central1"

results = {}


def attempt(name, fn):
    try:
        r = fn()
        r.raise_for_status()
        results[name] = f"OK {str(r.json())[:100]}"
    except Exception as e:  # noqa: BLE001
        results[name] = f"FAIL {type(e).__name__}: {str(e)[:160]}"


def main():
    attempt("reset", lambda: requests.post(f"{BASE}/__admin/reset"))
    net_link = f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/global/networks/tf-test-net"
    attempt("network", lambda: requests.post(
        f"{BASE}/compute/v1/projects/{PROJECT}/global/networks",
        json={"name": "tf-test-net", "description": "emulator test network",
              "autoCreateSubnetworks": False,
              "routingConfig": {"routingMode": "REGIONAL"}}))
    attempt("subnetwork", lambda: requests.post(
        f"{BASE}/compute/v1/projects/{PROJECT}/regions/{REGION}/subnetworks",
        json={"name": "tf-test-subnet", "description": "emulator test subnet",
              "network": net_link, "ipCidrRange": "10.10.0.0/24",
              "privateIpGoogleAccess": False}))
    attempt("firewall", lambda: requests.post(
        f"{BASE}/compute/v1/projects/{PROJECT}/global/firewalls",
        json={"name": "tf-test-allow-ssh", "description": "allow ssh (emulator)",
              "network": net_link, "priority": 1000, "direction": "INGRESS",
              "sourceRanges": ["0.0.0.0/0"],
              "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]}))
    attempt("bucket", lambda: requests.post(
        f"{BASE}/storage/v1/b", params={"project": PROJECT},
        json={"name": "tf-test-bucket", "location": "US", "storageClass": "STANDARD"}))
    attempt("service_account", lambda: requests.post(
        f"{BASE}/v1/projects/{PROJECT}/serviceAccounts",
        json={"accountId": "tf-test-sa",
              "serviceAccount": {"displayName": "Terraformer test SA (emulator)"}}))
    for k, v in results.items():
        print(f"{k:16} {v}")
    if any(v.startswith("FAIL") for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
