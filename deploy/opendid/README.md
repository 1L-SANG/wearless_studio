# OpenDID single-server deploy

Production layout:

```text
/opt/opendid/
  jars/{TA,Issuer,CA,Holder}/
  config/
  secrets/{TA,Issuer,CA,Wallet}/
  state/holder/
  state/migration/
```

Use only OpenDID V2.0.0 release jars for TA, Issuer, and CA, plus this repo's built Holder jar:

- `/opt/opendid/jars/TA/did-ta-server-2.0.0.jar`
- `/opt/opendid/jars/Issuer/did-issuer-server-2.0.0.jar`
- `/opt/opendid/jars/CA/did-ca-server-2.0.0.jar`
- `/opt/opendid/jars/Holder/fm-holder-0.1.0.jar`

Create the runtime user and directories:

```bash
sudo useradd --system --home /opt/opendid --shell /usr/sbin/nologin opendid
sudo install -d -o opendid -g opendid -m 0755 /opt/opendid/jars/{TA,Issuer,CA,Holder} /opt/opendid/config /opt/opendid/state/{holder,migration}
sudo install -d -o opendid -g opendid -m 0700 /opt/opendid/secrets/{TA,Issuer,CA,Wallet}
```

Copy configs and units:

```bash
sudo cp deploy/opendid/config/*.yml /opt/opendid/config/
sudo cp deploy/opendid/infra.compose.yml /opt/opendid/infra.compose.yml
sudo cp deploy/opendid/systemd/*.service /etc/systemd/system/
sudo install -o root -g opendid -m 0640 deploy/opendid/env.example /opt/opendid/opendid.env
sudo editor /opt/opendid/opendid.env
```

Start infra and apps:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now opendid-infra opendid-tas opendid-cas opendid-issuer fm-holder
```

Set `FM_HOLDER_BIND_ADDRESS` to Server 3's private IP before starting services. PostgreSQL `5432`, Besu RPC `8545/8546`, TAS `8090`, Issuer `8091`, and CAS `8094` bind loopback only. Holder `8100` binds only that private address; its HMAC authentication is an additional boundary, not a replacement for host and security-group rules allowing only Server 1.

Holder is a singleton: keep one non-templated `fm-holder.service` and one `FM_HOLDER_DATA_DIR`. Deploy with a bounded stop-then-start, never rolling overlap:

```bash
sudo systemctl stop fm-holder
sudo systemctl start fm-holder
```

Do not add a second Holder process until nonce cleanup has inter-process coordination.

`smoke.sh` defaults to the self-managed closed-port local fixture and requires an explicit `FM_HOLDER_HMAC_SECRET`. On an already-running Server 3, use only managed mode after systemd startup:

```bash
set -a
. /opt/opendid/opendid.env
set +a
sudo --preserve-env=FM_HOLDER_HMAC_SECRET,FM_HOLDER_BIND_ADDRESS \
  env OPENDID_SMOKE_MODE=managed deploy/opendid/smoke.sh
```

Managed mode also requires `FM_HOLDER_BIND_ADDRESS`; it health-checks existing services and restarts only `fm-holder`.

`opendid-infra.service` runs `docker compose ... up -d --wait`; Java services require it before startup.

Restore only onto a host where the named PostgreSQL/Besu volumes do not exist. Run the checksum and plan pass first, then apply as root so restored runtime files can be owned by `opendid:opendid`:

```bash
set -a; . /opt/opendid/opendid.env; set +a
sudo --preserve-env=OPENDID_POSTGRES_USER,OPENDID_POSTGRES_PASSWORD,OPENDID_POSTGRES_DB,OPENDID_POSTGRES_VOLUME,OPENDID_BESU_VOLUME \
  deploy/opendid/restore-state.sh /secure/path/export
sudo --preserve-env=OPENDID_POSTGRES_USER,OPENDID_POSTGRES_PASSWORD,OPENDID_POSTGRES_DB,OPENDID_POSTGRES_VOLUME,OPENDID_BESU_VOLUME \
  deploy/opendid/restore-state.sh /secure/path/export --apply
```

`OPENDID_OWNER` and `OPENDID_GROUP` override the default `opendid:opendid` runtime ownership. A failed apply removes only volumes and files created by that invocation; it never removes pre-existing state.
