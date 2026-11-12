# gcp-emulator

A local Google Cloud Platform emulator for testing
[Terraformer](https://github.com/Sun-Wukhan/terraformer) — built because
GCP, unlike AWS, has no official local emulator.

It implements the real GCP REST API paths and JSON shapes for a narrow
scope: Compute (regions, networks, subnetworks, firewalls), Cloud Storage
buckets, Resource Manager (v1/v3), plus stubs for Service Usage, Storage
Batch Operations and IAM service accounts, and a fake OAuth2 token
endpoint so Google client libraries complete their normal token flow
against it.

State is in-memory: restarting the emulator wipes it, so re-run the seed
after a restart.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py 8800   # serves http://127.0.0.1:8800
.venv/bin/python seed.py       # seeds project tf-test-project
```

The seed creates, through the emulator's own insert routes: network
`tf-test-net`, subnetwork `tf-test-subnet` (us-central1, 10.10.0.0/24),
firewall `tf-test-allow-ssh`, bucket `tf-test-bucket`, and one service
account.

Admin endpoints: `GET /__admin/health`, `POST /__admin/reset`.

## Credentials

`fake-sa.json` is a throwaway service-account file whose `token_uri`
points at the emulator's `/token` endpoint. The key was generated locally
for this purpose only: the account does not exist at Google, it is not a
real credential, and it grants access to nothing beyond this emulator.

## Use it with Terraform / Terraformer

With the `hashicorp/google` provider directly:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=$PWD/fake-sa.json
export GOOGLE_CLOUD_PROJECT=tf-test-project
export GOOGLE_REGION=us-central1
export GOOGLE_COMPUTE_CUSTOM_ENDPOINT=http://127.0.0.1:8800/compute/v1/
export GOOGLE_STORAGE_CUSTOM_ENDPOINT=http://127.0.0.1:8800/storage/v1/
```

Terraformer's GCP generators construct their Google API clients in code;
pointing them at the emulator additionally needs a build that honours the
`TERRAFORMER_GCP_EMULATOR_URL` environment variable.

## What it has already caught

Testing Terraformer against this emulator found a real bug on day one:
Terraformer emitted `google_storage_bucket_acl`,
`google_storage_default_object_acl` and `google_storage_object_acl`
resources that the google provider cannot import at all (no importer in
provider v8.11), so every GCS import recorded guaranteed failures. Fixed
in [terraformer PR #31](https://github.com/Sun-Wukhan/terraformer/pull/31);
the per-entity `*_access_control` resources carry the same ACL data in
importable form.
