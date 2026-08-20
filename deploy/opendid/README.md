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

Only localhost ports are bound: PostgreSQL `5432`, Besu RPC `8545/8546`, OpenDID apps `8090/8091/8094`, Holder `8100`. Block external access at the host firewall if Docker is configured to publish beyond loopback.

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
