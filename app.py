#!/usr/bin/env python3
"""gcp-emulator: a local Google Cloud Platform emulator for testing Terraformer.

Implements just enough of the real GCP REST APIs (same paths, same JSON
shapes) for Terraformer's discovery + the google provider's refresh reads:

  - Compute Engine v1: regions, networks, subnetworks, firewalls
  - Cloud Storage v1 (JSON API): buckets (+ iam/acl/objects stubs)
  - Resource Manager v1/v3: project get, getIamPolicy, getAncestry
  - Service Usage v1: services list (empty)
  - Storage Batch Operations v1: jobs list (empty)
  - IAM v1 (REST surface only): service accounts list/get/create
  - POST /token: fake OAuth2 token endpoint for service-account JWT flow

State is in-memory. Seed through the real insert routes (see seed.py).
Every request is logged to stderr so unimplemented calls are visible.
"""

import itertools
import sys
import threading
import time

from flask import Flask, jsonify, request

app = Flask(__name__)

HOST = "http://127.0.0.1:8800"
PROJECT_NUMBER = "123456789012"
TS = "2026-01-01T00:00:00.000-07:00"

_lock = threading.Lock()
_ids = itertools.count(1000000000000000000)

# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
store = {
    "networks": {},     # (project, name) -> obj
    "firewalls": {},    # (project, name) -> obj
    "subnetworks": {},  # (project, region, name) -> obj
    "buckets": {},      # name -> obj
    "service_accounts": {},  # (project, email) -> obj
    "projects": {},     # project_id -> obj (crm v1 shape)
}


def reset_store():
    with _lock:
        for v in store.values():
            v.clear()


def gerr(code, status, message):
    return jsonify({"error": {"code": code, "message": message,
                               "errors": [{"domain": "global", "reason": status.lower(),
                                           "message": message}],
                               "status": status}}), code


def not_found(what):
    return gerr(404, "NOT_FOUND", f"The resource '{what}' was not found")


@app.before_request
def log_request():
    print(f"[emu] {request.method} {request.path}", file=sys.stderr, flush=True)


@app.get("/__admin/health")
def health():
    return "ok"


@app.post("/__admin/reset")
def reset():
    reset_store()
    return jsonify({"reset": True})


# ---------------------------------------------------------------------------
# fake oauth2 token endpoint (service-account JWT bearer flow lands here)
# ---------------------------------------------------------------------------
@app.post("/token")
def token():
    return jsonify({"access_token": "emulator-access-token",
                    "expires_in": 3600, "token_type": "Bearer"})


# ---------------------------------------------------------------------------
# compute v1
# ---------------------------------------------------------------------------
REGIONS = {
    "us-central1": ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"],
    "us-east1": ["us-east1-b", "us-east1-c", "us-east1-d"],
    "europe-west1": ["europe-west1-b", "europe-west1-c", "europe-west1-d"],
}


def region_obj(project, name):
    return {
        "kind": "compute#region",
        "id": str(next(_ids)),
        "creationTimestamp": TS,
        "name": name,
        "description": f"{name} region (emulator)",
        "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{name}",
        "zones": [f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{z}"
                  for z in REGIONS.get(name, [name + "-a"])],
        "quotas": [],
        "status": "UP",
    }


@app.get("/compute/v1/projects/<project>/regions")
def regions_list(project):
    return jsonify({"kind": "compute#regionList", "id": f"projects/{project}/regions",
                    "items": [region_obj(project, r) for r in REGIONS],
                    "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/regions"})


@app.get("/compute/v1/projects/<project>/regions/<region>")
def regions_get(project, region):
    if region not in REGIONS:
        return not_found(region)
    return jsonify(region_obj(project, region))


def operation(project, optype, target_link, target_id):
    return jsonify({"kind": "compute#operation", "id": str(next(_ids)),
                    "name": f"operation-{int(time.time())}",
                    "operationType": optype, "targetLink": target_link,
                    "targetId": str(target_id), "status": "DONE", "progress": 100,
                    "insertTime": TS, "startTime": TS, "endTime": TS,
                    "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/global/operations/op"})


def net_selflink(project, name):
    return f"https://www.googleapis.com/compute/v1/projects/{project}/global/networks/{name}"


@app.get("/compute/v1/projects/<project>/global/networks")
def networks_list(project):
    items = [o for (p, _), o in store["networks"].items() if p == project]
    return jsonify({"kind": "compute#networkList", "id": f"projects/{project}/global/networks",
                    "items": items,
                    "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/global/networks"})


@app.get("/compute/v1/projects/<project>/global/networks/<name>")
def networks_get(project, name):
    obj = store["networks"].get((project, name))
    return jsonify(obj) if obj else not_found(name)


@app.post("/compute/v1/projects/<project>/global/networks")
def networks_insert(project):
    body = request.get_json(force=True)
    name = body["name"]
    obj = {
        "kind": "compute#network",
        "id": str(next(_ids)),
        "creationTimestamp": TS,
        "name": name,
        "description": body.get("description", ""),
        "selfLink": net_selflink(project, name),
        "autoCreateSubnetworks": body.get("autoCreateSubnetworks", False),
        "routingConfig": body.get("routingConfig", {"routingMode": "REGIONAL"}),
        "mtu": body.get("mtu", 1460),
        "networkFirewallPolicyEnforcementOrder": "AFTER_CLASSIC_FIREWALL",
        "subnetworks": [],
    }
    if obj["autoCreateSubnetworks"] is False:
        obj["gatewayIPv4"] = "10.128.0.1"
    with _lock:
        store["networks"][(project, name)] = obj
    return operation(project, "insert", obj["selfLink"], obj["id"])


@app.get("/compute/v1/projects/<project>/global/firewalls")
def firewalls_list(project):
    items = [o for (p, _), o in store["firewalls"].items() if p == project]
    return jsonify({"kind": "compute#firewallList", "id": f"projects/{project}/global/firewalls",
                    "items": items,
                    "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/global/firewalls"})


@app.get("/compute/v1/projects/<project>/global/firewalls/<name>")
def firewalls_get(project, name):
    obj = store["firewalls"].get((project, name))
    return jsonify(obj) if obj else not_found(name)


@app.post("/compute/v1/projects/<project>/global/firewalls")
def firewalls_insert(project):
    body = request.get_json(force=True)
    name = body["name"]
    obj = {
        "kind": "compute#firewall",
        "id": str(next(_ids)),
        "creationTimestamp": TS,
        "name": name,
        "description": body.get("description", ""),
        "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/global/firewalls/{name}",
        "network": body.get("network", ""),
        "priority": body.get("priority", 1000),
        "direction": body.get("direction", "INGRESS"),
        "sourceRanges": body.get("sourceRanges", []),
        "destinationRanges": body.get("destinationRanges", []),
        "sourceTags": body.get("sourceTags", []),
        "targetTags": body.get("targetTags", []),
        "allowed": body.get("allowed", []),
        "denied": body.get("denied", []),
        "disabled": body.get("disabled", False),
        "logConfig": {"enable": False, "metadata": "EXCLUDE_ALL_METADATA"},
    }
    with _lock:
        store["firewalls"][(project, name)] = obj
    return operation(project, "insert", obj["selfLink"], obj["id"])


def subnet_selflink(project, region, name):
    return f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks/{name}"


@app.get("/compute/v1/projects/<project>/regions/<region>/subnetworks")
def subnetworks_list(project, region):
    items = [o for (p, r, _), o in store["subnetworks"].items() if p == project and r == region]
    return jsonify({"kind": "compute#subnetworkList",
                    "id": f"projects/{project}/regions/{region}/subnetworks",
                    "items": items,
                    "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks"})


@app.get("/compute/v1/projects/<project>/regions/<region>/subnetworks/<name>")
def subnetworks_get(project, region, name):
    obj = store["subnetworks"].get((project, region, name))
    return jsonify(obj) if obj else not_found(name)


@app.post("/compute/v1/projects/<project>/regions/<region>/subnetworks")
def subnetworks_insert(project, region):
    body = request.get_json(force=True)
    name = body["name"]
    cidr = body.get("ipCidrRange", "10.0.0.0/24")
    gateway = cidr.split("/")[0].rsplit(".", 1)[0] + ".1"
    obj = {
        "kind": "compute#subnetwork",
        "id": str(next(_ids)),
        "creationTimestamp": TS,
        "name": name,
        "description": body.get("description", ""),
        "selfLink": subnet_selflink(project, region, name),
        "network": body.get("network", ""),
        "ipCidrRange": cidr,
        "gatewayAddress": gateway,
        "region": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}",
        "privateIpGoogleAccess": body.get("privateIpGoogleAccess", False),
        "purpose": "PRIVATE",
        "role": "ACTIVE",
        "fingerprint": "emulator-fingerprint",
        "stackType": "IPV4_ONLY",
    }
    with _lock:
        store["subnetworks"][(project, region, name)] = obj
        net_name = obj["network"].rstrip("/").split("/")[-1]
        net = store["networks"].get((project, net_name))
        if net is not None and obj["selfLink"] not in net["subnetworks"]:
            net["subnetworks"].append(obj["selfLink"])
    return operation(project, "insert", obj["selfLink"], obj["id"])


# ---------------------------------------------------------------------------
# storage v1 (JSON API)
# ---------------------------------------------------------------------------
@app.get("/storage/v1/b")
def buckets_list():
    items = list(store["buckets"].values())
    return jsonify({"kind": "storage#buckets", "items": items})


@app.get("/storage/v1/b/<bucket>")
def buckets_get(bucket):
    obj = store["buckets"].get(bucket)
    return jsonify(obj) if obj else not_found(bucket)


@app.post("/storage/v1/b")
def buckets_insert():
    body = request.get_json(force=True)
    name = body["name"]
    obj = {
        "kind": "storage#bucket",
        "id": name,
        "selfLink": f"https://www.googleapis.com/storage/v1/b/{name}",
        "projectNumber": PROJECT_NUMBER,
        "name": name,
        "bucket": name,
        "metageneration": "1",
        "location": body.get("location", "US"),
        "storageClass": body.get("storageClass", "STANDARD"),
        "timeCreated": TS,
        "updated": TS,
        "locationType": "multi-region" if body.get("location", "US") == "US" else "region",
        "iamConfiguration": {
            "bucketPolicyOnly": {"enabled": False},
            "uniformBucketLevelAccess": {"enabled": False},
            "publicAccessPrevention": "inherited",
        },
        "versioning": body.get("versioning", {"enabled": False}),
        "defaultEventBasedHold": False,
        "etag": "CAE=",
    }
    with _lock:
        store["buckets"][name] = obj
    return jsonify(obj)


@app.get("/storage/v1/b/<bucket>/iam")
def bucket_iam(bucket):
    if bucket not in store["buckets"]:
        return not_found(bucket)
    return jsonify({"kind": "storage#policy", "resourceId": f"projects/_/buckets/{bucket}",
                    "bindings": [], "etag": "CAE="})


@app.get("/storage/v1/b/<bucket>/acl")
def bucket_acl(bucket):
    return jsonify({"kind": "storage#bucketAccessControls", "items": []})


@app.get("/storage/v1/b/<bucket>/o")
def objects_list(bucket):
    return jsonify({"kind": "storage#objects", "items": []})


# ---------------------------------------------------------------------------
# resource manager v1 + v3
# ---------------------------------------------------------------------------
def project_obj(pid):
    return store["projects"].get(pid) or {
        "projectNumber": PROJECT_NUMBER,
        "projectId": pid,
        "lifecycleState": "ACTIVE",
        "name": pid,
        "createTime": TS,
        "parent": {"type": "organization", "id": "123456789"},
    }


@app.get("/v1/projects/<pid>")
def crm_v1_get(pid):
    return jsonify(project_obj(pid))


@app.get("/v3/projects/<pid>")
def crm_v3_get(pid):
    p = project_obj(pid)
    return jsonify({"name": f"projects/{pid}", "projectId": pid,
                    "projectNumber": p["projectNumber"], "displayName": p["name"],
                    "state": "ACTIVE", "createTime": TS,
                    "parent": "organizations/123456789"})


@app.post("/v1/projects/<path:pid>:getIamPolicy")
def crm_v1_policy(pid):
    return jsonify({"version": 1, "bindings": [], "etag": "BwWEmulator="})


@app.post("/v3/projects/<path:pid>:getIamPolicy")
def crm_v3_policy(pid):
    return jsonify({"version": 1, "bindings": [], "etag": "BwWEmulator="})


@app.post("/v1/projects/<path:pid>:getAncestry")
def crm_v1_ancestry(pid):
    return jsonify({"ancestor": [
        {"resourceId": {"type": "project", "id": pid}},
        {"resourceId": {"type": "organization", "id": "123456789"}},
    ]})


# ---------------------------------------------------------------------------
# service usage v1 / storage batch operations v1: empty collections
# ---------------------------------------------------------------------------
@app.get("/serviceusage/v1/projects/<pid>/services")
def su_services(pid):
    return jsonify({"services": []})


@app.get("/batch/v1/projects/<pid>/locations/<loc>/jobs")
def batch_jobs(pid, loc):
    return jsonify({"jobs": []})


# ---------------------------------------------------------------------------
# iam v1 REST surface (service accounts) -- note: Terraformer's iam
# generator primarily uses the gRPC admin client, which this emulator
# does not serve; these routes cover REST callers and the terraform
# provider's reads if pointed here.
# ---------------------------------------------------------------------------
@app.get("/v1/projects/<pid>/serviceAccounts")
def sa_list(pid):
    items = [o for (p, _), o in store["service_accounts"].items() if p == pid]
    return jsonify({"accounts": items})


@app.get("/v1/projects/<pid>/serviceAccounts/<path:email>")
def sa_get(pid, email):
    obj = store["service_accounts"].get((pid, email))
    return jsonify(obj) if obj else not_found(email)


@app.post("/v1/projects/<pid>/serviceAccounts")
def sa_create(pid):
    body = request.get_json(force=True)
    account_id = body.get("accountId", "emu")
    email = f"{account_id}@{pid}.iam.gserviceaccount.com"
    obj = {
        "name": f"projects/{pid}/serviceAccounts/{email}",
        "projectId": pid,
        "uniqueId": str(next(_ids))[:21],
        "email": email,
        "displayName": body.get("serviceAccount", {}).get("displayName", account_id),
        "description": body.get("serviceAccount", {}).get("description", ""),
        "disabled": False,
        "oauth2ClientId": str(next(_ids))[:21],
        "etag": "MDEwMjE2MjI=",
    }
    with _lock:
        store["service_accounts"][(pid, email)] = obj
    return jsonify(obj)


@app.post("/v1/projects/<pid>/serviceAccounts/<path:email>:getIamPolicy")
def sa_policy(pid, email):
    return jsonify({"version": 1, "bindings": [], "etag": "BwWEmulator="})


@app.get("/v1/roles")
def roles_list():
    return jsonify({"roles": []})


@app.get("/v1/projects/<pid>/roles")
def project_roles_list(pid):
    return jsonify({"roles": []})


@app.errorhandler(404)
def handle_404(e):
    return not_found(request.path)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    app.run(host="127.0.0.1", port=port, threaded=True)
