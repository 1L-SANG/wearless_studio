# OpenDID single-server deployment files

Target layout:

```text
/opt/opendid/
  jars/{TA,Issuer,CA,Holder}/
  config/
  secrets/{TA,Issuer,CA,Wallet}/
  state/holder/
  state/migration/
```

Use Java 21, OpenDID V2.0.0 jars, and the Holder jar built by Task 1:

```text
/opt/opendid/jars/TA/did-ta-server-2.0.0.jar
/opt/opendid/jars/Issuer/did-issuer-server-2.0.0.jar
/opt/opendid/jars/CA/did-ca-server-2.0.0.jar
/opt/opendid/jars/Holder/fm-holder-0.1.0.jar
```

Secrets stay outside Git. Create the directories as the runtime user and keep them owner-only:

```bash
sudo install -d -o opendid -g opendid -m 0755 /opt/opendid/{jars,config,state,state/holder,state/migration}
sudo install -d -o opendid -g opendid -m 0700 /opt/opendid/secrets/{TA,Issuer,CA,Wallet}
sudo chmod 0600 /opt/opendid/secrets/*/*.env /opt/opendid/secrets/*/*.wallet /opt/opendid/secrets/*/*.zkpwallet /opt/opendid/secrets/*/blockchain.properties
```

Copy `deploy/opendid/env.example` values into the matching secret env files and replace every placeholder before starting services. The deploy config intentionally has no Holder pepper or wallet password fallback.

Run infrastructure first:

```bash
docker compose -f deploy/opendid/infra.compose.yml --env-file /opt/opendid/secrets/TA/postgres.env up -d
```

Restore PostgreSQL and Besu state into the stable named volumes before enabling Java services. Then install the config and units:

```bash
sudo install -o opendid -g opendid -m 0640 deploy/opendid/config/*.yml /opt/opendid/config/
sudo install -o root -g root -m 0644 deploy/opendid/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opendid-tas opendid-issuer opendid-cas fm-holder
```

Only Server 1 should reach Holder `:8100`. PostgreSQL, Besu, TAS, Issuer, CAS, and Orchestrator ports stay closed externally; Orchestrator is not a steady-state service here.
