# deploy/certs — сертификат НУЦ Минцифры

API MAX (`platform-api2.max.ru`) и GigaChat подписаны Национальным удостоверяющим центром Минцифры.
Его корневого сертификата нет в системном хранилище контейнеров, поэтому бот и агент берут его отсюда
(`docker-compose.stand.yml` монтирует папку в `/certs`, пути — `BOT_MAX_CA_BUNDLE` и `LLM_GIGACHAT_CA_BUNDLE`).

На сервере стенда, из корня репозитория:

```bash
curl -fsSL https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt -o deploy/certs/russian_trusted_root_ca.pem
```

Ссылка — со страницы «Сертификаты Минцифры» на Госуслугах; если она поменялась, скачайте корневой
сертификат в формате PEM оттуда. Файл в git не кладём (`.gitignore`); `make stand-config` проверяет, что он на месте.
