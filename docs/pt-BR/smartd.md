# smartd: alerta de disco com problema

[English](../en/smartd.md) · **Português** · [← README](../../README.pt-BR.md)

Os discos (HD, SSD, NVMe) registram a própria saúde no **S.M.A.R.T.**: setores realocados, erros de leitura, temperatura, desgaste.
O `smartd` (pacote `smartmontools`) vigia esses dados, roda testes periódicos e chama um script quando algo piora.
Este tutorial faz o smartd avisar o cartão **Backups e discos** do hub.

## 1. Instalar

```bash
sudo apt install --no-install-recommends smartmontools
```

O `--no-install-recommends` evita que o apt instale um servidor de e-mail (postfix), que não é necessário aqui.

## 2. Conhecer os discos

```bash
lsblk -d -o NAME,MODEL,SIZE,ROTA,TRAN      # ROTA=1 é HD; TRAN mostra sata/nvme/usb
sudo smartctl -i /dev/sda                  # modelo, série, se o SMART está ligado
sudo smartctl -H /dev/sda                  # avaliação geral: PASSED / FAILED
sudo smartctl -A /dev/sda                  # atributos (veja Reallocated_Sector_Ct, Pending, CRC)
```

Discos USB às vezes precisam de `-d sat` (ex.: `sudo smartctl -d sat -H /dev/sdb`). Alguns adaptadores USB não repassam o SMART.

## 3. Instalar os scripts do hub

Na pasta do hub (pule o que já fez no tutorial do Backrest):

```bash
sudo install -m 755 scripts/hub-notify /usr/local/bin/hub-notify
sudo install -m 755 scripts/smartd-hub-notify /usr/local/bin/smartd-hub-notify
echo "HUB_TOKEN_FILE=$PWD/data/notify_token" | sudo tee /etc/hub-notify.env
```

São **cópias** de propósito: o smartd roda como root, então o script que ele executa não pode ser um arquivo que o seu usuário edita.

## 4. Configurar o smartd

```bash
sudo cp /etc/smartd.conf /etc/smartd.conf.orig
sudo nano /etc/smartd.conf
```

Comente (com `#`) a linha `DEVICESCAN` que já existe e adicione no fim, **em uma linha só**:

```
DEVICESCAN -a -o on -S on -n standby,q -s (S/../.././02|L/../../6/03) -W 4,45,55 -m <nomailer> -M daily -M exec /usr/local/bin/smartd-hub-notify
```

| Opção | |
|---|---|
| `DEVICESCAN` | vigia todos os discos encontrados |
| `-a` | todas as verificações padrão (saúde, atributos, log de erros, testes) |
| `-o on -S on` | coleta offline e salvamento automático dos atributos |
| `-n standby,q` | não acorda HD dormindo só para checar |
| `-s (S/../.././02\|L/../../6/03)` | teste **curto** todo dia às 2h e **longo** todo sábado às 3h |
| `-W 4,45,55` | avisa variação de 4 °C, informa a 45 °C e alerta a 55 °C |
| `-m <nomailer>` | não manda e-mail, só o `-M exec` |
| `-M daily` | se o problema continuar, repete o aviso uma vez por dia |
| `-M exec …` | chama o script que envia para o hub |

Para um disco USB que precisa de `-d sat`, troque o `DEVICESCAN` por uma linha por disco, usando um nome estável:

```
/dev/disk/by-id/usb-Marca_Modelo_123456-0:0 -d sat -a -o on -S on -n standby,q -s (S/../.././02|L/../../6/03) -W 4,45,55 -m <nomailer> -M daily -M exec /usr/local/bin/smartd-hub-notify
```

## 5. Testar

Acrescente `-M test` no fim da linha, temporariamente, e reinicie o serviço:

```bash
sudo systemctl restart smartmontools
journalctl -u smartmontools -n 30 --no-pager
```

Para cada disco, deve aparecer um item **Disco /dev/…** como *ok* ("teste do smartd: notificação funcionando") no cartão **Backups e discos**.
Depois, **tire o `-M test`**, reinicie de novo e remova os itens de teste com **✕**.

## 6. Quando chegar um alerta

| Tipo | Nível no hub | O que fazer |
|---|---|---|
| `Health` (FAILED) | erro | o disco está avisando que vai falhar: **confira o backup agora** e troque o disco |
| `CurrentPendingSector`, `OfflineUncorrectableSector` | erro | setores ruins. Rode `sudo smartctl -t long /dev/sdX` e acompanhe; se aumentarem, troque |
| `ErrorCount`, `SelfTest` | aviso | veja `sudo smartctl -a /dev/sdX`. Erros de CRC costumam ser **cabo** ruim |
| `Temperature` | aviso | ventilação, poeira, disco encostado em outro |

Depois de verificar, clique em **✓** no hub para marcar como resolvido. Se o problema continuar, o `-M daily` avisa de novo no dia seguinte.
