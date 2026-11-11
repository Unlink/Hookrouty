# Hookrouty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for implementation in the current session, or superpowers:subagent-driven-development if delegated execution is explicitly chosen. Steps use checkbox syntax for tracking. If those workflows are unavailable, follow the same tasks and verification steps directly; no external skill is needed to understand this specification.

**Cieľ:** Malá univerzálna webhook proxy v Pythone so správou používateľov a hookov cez web, parametrizovanými URL, konfigurovateľnými cieľovými HTTP požiadavkami a monitoringom.

**Architektúra:** Jedna FastAPI aplikácia obsluhuje web pod /app, JSON API na správu pod /api a verejné hooky pod /hooks. HTML renderuje Jinja2, sieťové volania vykonáva zdieľaný HTTPX AsyncClient a dáta ukladá SQLite. Volajúci čaká na odpoveď cieľa. Nasadenie tvorí jeden Docker kontajner a dátový volume.

**Technológie:** Python 3.13, FastAPI, Uvicorn, Jinja2, HTTPX, aiosqlite, pydantic-settings, argon2-cffi a cryptography. Overenie: pytest, pytest-asyncio, Ruff a Playwright. Kompatibilné verzie sa uzamknú v uv.lock pri implementácii prvej úlohy; runtime neobsahuje vývojové závislosti.

**Špecifikácia:** Funkčné požiadavky, kontrakty, rozhodnutia aj implementačný plán sú úplne obsiahnuté v tomto súbore. Nie je potrebná história konverzácie ani samostatný dokument.

**Pracovný názov:** Hookrouty. **Balík:** hookrouty. **Verzia rozsahu:** v1. **Dátum:** 11. november 2026. Toto je zadanie na implementáciu; aplikácia ani image zatiaľ nie sú týmto dokumentom vytvorené.

## Obsah

- [Rozsah a rozhodnutia](#rozsah-a-rozhodnutia)
- [Globálne hranice](#globálne-hranice)
- [Funkčná špecifikácia](#funkčná-špecifikácia)
- [Kontrakty pre implementáciu](#kontrakty-pre-implementáciu)
- [Konfigurácia a nasadenie](#konfigurácia-a-nasadenie)
- [Súbory a zodpovednosti](#súbory-a-zodpovednosti)
- [Dátový model a rozhrania](#dátový-model-a-rozhrania)
- [Zameranie kontroly](#zameranie-kontroly)
- [Implementačné úlohy](#implementačné-úlohy)
- [Hotový výsledok](#hotový-výsledok)
- [Konečné akceptačné kritériá](#konečné-akceptačné-kritériá)
- [Odovzdanie implementátorovi](#odovzdanie-implementátorovi)


## Rozsah a rozhodnutia

Prvá verzia má používateľské účty, správu hookov, parametrizované vstupné URL, konfiguráciu jedného HTTP cieľa na hook, jednoduché šablóny a monitoring. Nasadzuje sa jedným Compose súborom s jedným kontajnerom a dátovým volume. Prevádzkovateľ nemusí pripravovať lokálne konfiguračné JSON súbory.

| Rozhodnutie | Správanie prvej verzie |
| --- | --- |
| Používatelia | Admin vidí všetko; user iba pridelené hooky a ich históriu |
| Neuzavretá voľba oprávnení | Oddelenie podľa pridelenia je zvolený predvolený variant, nie výslovná požiadavka používateľa |
| Vytvorenie hooku | Môže ho vytvoriť aj user; automaticky získa pridelenie |
| Pridelenie ďalším účtom | Mení iba admin |
| Bootstrap admin | Len pri prázdnej databáze; env neprepisuje existujúce účty |
| Cieľová URL | Pevná schéma, hostname a port; šablóny v ceste, query, hlavičkách a tele |
| Vykonanie | Synchrónne z pohľadu volajúceho, jeden pokus, bez automatického retry |
| Odpoveď | Status a telo cieľa vrátane 4xx/5xx, filtrované koncové hlavičky |
| Monitoring | Metadáta vždy; telá voliteľne na hooku a predvolene vypnuté |

Nasadenie slúži jednej dôveryhodnej skupine používateľov. Pridelenie správy hooku oprávňuje používateľa meniť aj jeho cieľové volanie. Verejné hooky používajú svoje tokeny, nezávisle od prihlasovacích účtov. Každý hook má jeden cieľ; workflow, shell príkazy, plánovač a distribuovaná fronta nie sú súčasťou tejto verzie.

## Globálne hranice

| Oblasť | Presná hodnota alebo pravidlo |
| --- | --- |
| Metódy | GET, POST, PUT, PATCH, DELETE, HEAD; jedna vstupná metóda na hook |
| Cesta | Prefix `/hooks/`, parameter `:meno` zodpovedá jednému segmentu |
| Vstupná autentifikácia | Povinný token v `X-Webhook-Token` alebo zvolený query parameter `token` |
| HTTP schémy | HTTP a HTTPS vrátane interných LAN a Docker adries |
| Timeout | Celkový deadline 60 sekúnd, nastaviteľný na hooku od 1 do 900 sekúnd |
| Veľkosť | Vstupné telo 1 MiB, zostavené telo 1 MiB, odpoveď cieľa 8 MiB |
| Súbežnosť | 20 cieľových volaní globálne; pri plnej kapacite okamžite 503 |
| Presmerovania a opakovanie | HTTP klient ich automaticky nevykonáva |
| História | Najviac 7 dní aj 10 000 ukončených záznamov globálne; periodické čistenie |
| Zachytené telo | Najviac 16 KiB z každého textového alebo JSON tela, až po maskovaní |
| Sessions | 24 hodín, cookie HttpOnly a SameSite=Lax; Secure podľa APP_URL |
| Prihlásenie | Najviac 5 neúspechov za 15 minút podľa mena účtu aj IP |
| Úložisko | SQLite vo `/data`, WAL, jeden Uvicorn worker |
| Správca | Posledného aktívneho admina nemožno deaktivovať ani degradovať |
| Tajomstvá | Argon2id pre heslá, hash tokenov, autentifikované šifrovanie tajných cieľových polí |
| Prevádzka | APP_SECRET_KEY povinný vždy; ADMIN_PASSWORD len pri inicializácii prázdnej databázy |

Hodnoty veľkostí, súbežnosti a retencie sa dajú zmeniť cez env. Funkčná špecifikácia a kontrakty nižšie určujú maskovanie, chyby a správanie pri odpojení klienta. Timeout neurčuje výsledok vzdialenej akcie: tá už mohla prebehnúť.

## Funkčná špecifikácia

### Správa hookov

Správca môže hook vytvoriť, upraviť, duplikovať, zapnúť alebo vypnúť, zmeniť token, otestovať a zmazať. Každý hook má stabilné interné ID, názov, popis, vstupnú metódu, vzor vstupnej cesty, nastavenie vstupnej autentifikácie, cieľovú metódu a URL, query parametre, hlavičky, nastavenie tela, celkový timeout a nastavenie zachytávania tiel do histórie.

Povolené metódy: GET, POST, PUT, PATCH, DELETE a HEAD. Jedna vstupná metóda na hook; cieľová metóda sa nastavuje samostatne. GET a HEAD do cieľa nemajú telo. Všetky hooky sú pod /hooks/. Parametre používajú syntax :meno a zodpovedajú jednému segmentu, napríklad POST /hooks/deploy/:project/:environment.

Párovanie cesty je deterministické: pevný segment má prednosť pred parametrom na rovnakej pozícii. Pri ukladaní sa odmietnu prekrývajúce sa vzory rovnakej priority vrátane vzorov odlišných len menami parametrov. Metóda je súčasťou routovania. Koncové lomítko sa rozlišuje; automatické presmerovanie sa nepoužíva. Vypnutý hook odpovedá 404. Neznáma cesta odpovedá 404, nesprávna metóda existujúcej cesty 405.

Token sa vygeneruje kryptograficky bezpečne alebo zadá ručne. Prichádza cez X-Webhook-Token; správca môže namiesto toho zvoliť query parameter token. Je povinný a kontroluje sa až pre jednoznačne vybraný hook, bez skúšania iných hookov pri chybe. Ukladá sa len jeho hash, token sa zobrazí pri vytvorení alebo zmene. Duplikovanie vytvorí nový token a vyžaduje novú vstupnú cestu; kópia je predvolene vypnutá. Neplatný alebo chýbajúci token vracia 401.

### Konfigurácia cieľovej požiadavky

Šablóny podporujú len odkazy na hodnoty: {{path.project}}, {{query.version}}, {{headers.X-Event}} a {{body.release.tag}}. Hlavičky sa vyhľadávajú bez ohľadu na veľkosť písmen. Pri opakovanom query parametri sa použije prvá hodnota. Body odkazy sú dostupné len pre platný JSON; bodková cesta podporuje kľúče objektov. Odkazy do prvkov polí a na kľúče obsahujúce bodku nie sú súčasťou v1.

Odkazy sa dajú použiť v ceste cieľovej URL, hodnotách samostatne nakonfigurovaných query parametrov, hodnotách hlavičiek a v tele. Schéma, hostname a port cieľa zostávajú pevne nakonfigurované správcom; vstupné dáta ich nemenia. Povolené schémy sú HTTP a HTTPS, vrátane interných LAN a Docker adries. Toto je zvolená hranica templatingu URL vo v1.

Hodnoty v URL sa zakódujú pre príslušný segment alebo query parameter. Hlavičky obsahujúce CR alebo LF sa odmietnu. Chýbajúca hodnota, neplatný JSON potrebný pre šablónu alebo neplatný výsledok vracia 400 ešte pred volaním cieľa. Syntakticky nesprávna konfigurácia sa odmietne už pri ukladaní hooku.

Telo má tri režimy: žiadne telo, pôvodné telo v nezmenených bajtoch a vlastná šablóna. Šablóna môže byť textová alebo JSON. JSON sa spracuje štruktúrovane: samostatný odkaz ako hodnota zachová JSON typ zdrojovej hodnoty; odkaz vložený do textového reťazca vloží jeho textovú hodnotu a serializer zabezpečí escapovanie. Objekt ani pole sa nevkladajú do textu automaticky; nekompatibilné použitie skončí chybou 400. Pevne nastavené telo je šablóna bez odkazov. Rozhranie poskytne ukážku výslednej požiadavky so vzorovými dátami pred odoslaním testu.

Vstupná autorizácia sa automaticky nepreposiela. Do cieľa sa posielajú len nakonfigurované hlavičky a nevyhnutné protokolové hlavičky. V režime pôvodného tela sa zachová Content-Type, ak správca nenastaví vlastný. Content-Length a Host určuje HTTP klient; hop-by-hop hlavičky sa nekopírujú. Hlavičky označené ako tajné sa v editore maskujú, ich prázdne ponechanie pri úprave zachová existujúcu hodnotu a odstránenie má samostatnú akciu.

### Priebeh volania a chyby

Po routovaní a autentifikácii sa overí požiadavka, zostaví cieľové volanie a uloží záznam running. Potom aplikácia vykoná jeden HTTP pokus. Celkový timeout zahŕňa pripojenie aj prečítanie odpovede; jeho predvolená hodnota je 60 sekúnd a na hooku ho možno nastaviť od 1 do 900 sekúnd. HTTPX AsyncClient je zdieľaný v rámci procesu. HTTPS overuje certifikát a presmerovania sa automaticky nenasledujú.

Volajúci dostane status a telo cieľovej odpovede vrátane 4xx a 5xx. Zachovajú sa bežné koncové hlavičky, napríklad Content-Type a Location; odstránia sa hop-by-hop hlavičky a Set-Cookie, Content-Length sa vypočíta znova. Implementátor otestuje, že prípadná dekompresia HTTP klientom zostane konzistentná s Content-Encoding a dĺžkou odpovede. Odpoveď sa najskôr načíta do limitovaného bufferu; nejde o streaming.

Lokálne chyby používajú JSON s bezpečnou správou a request_id: nesprávny vstup 400, token 401, veľký vstup 413, sieťová alebo TLS chyba cieľa 502, prekročenie limitu cieľovej odpovede 502, vyčerpanie kapacity 503 a celkový timeout 504. Každá odpoveď nesie X-Hookrouty-Request-Id. Limit vstupného aj zostaveného tela je predvolene 1 MiB, cieľovej odpovede 8 MiB. Kapacita: 20 súčasných cieľových volaní globálne, bez čakania vo fronte; ďalšie volanie dostane 503. Požiadavky na ten istý hook môžu bežať súčasne. Limity sú nastaviteľné cez env.

Automatické opakovanie je vypnuté. Timeout alebo odpojenie klienta neznamená, že sa akcia v cieľovej službe neuskutočnila; detail volania výslovne uvedie neistý výsledok, ak odpoveď nebola získaná. Po reštarte sa zostávajúce running záznamy označia interrupted a neposielajú znovu. Odpojenie klienta ukončí lokálne čakanie a zaznamená client_disconnected; už odoslanú vzdialenú akciu tým aplikácia nevie odvolať.

### Monitoring a história

Prehľad ukazuje počet volaní, úspešnosť a priemerné trvanie za 24 hodín a 7 dní; filter umožní vybrať hook a obdobie. Úspech znamená cieľový HTTP status 2xx. Presmerovania 3xx sú samostatná kategória, 4xx a 5xx chyby cieľa. Do menovateľa úspešnosti patria len volania, ktoré sa pokúsili kontaktovať cieľ; lokálne odmietnutia sa počítajú oddelene. Meria sa aj počet práve bežiacich volaní. Zoznam sa obnovuje jednoduchým pollingom.

História má stránkovanie a filtre podľa hooku, času a výsledku. Každý záznam má request_id, hook ID a názov v čase volania, čas začiatku, trvanie, cieľový status a interný výsledok vrátane running, success, redirected, upstream_error, rejected, proxy_error, timeout, client_disconnected a interrupted. Pri lokálnych odmietnutiach sa zaznamená len bezpečná metainformácia, bez tiel alebo hodnôt tokenov; neznáme cesty sa vedú len ako agregovaný počet, aby nezaplavili databázu.

Detail zobrazuje povolené vstupné parametre, bezpečne zobrazený cieľ požiadavky, hlavičky s maskovaním, výsledok a chybu. Zachytávanie tiel je voliteľné pre každý hook a predvolene vypnuté; po zapnutí sa ukladá najviac 16 KiB textového alebo JSON tela z každej zo vstupnej požiadavky, cieľovej požiadavky a odpovede. Väčší obsah sa označí ako skrátený, binárne telá sa neukladajú. Odpoveď volajúcemu sa týmto limitom histórie neskracuje.

Authorization, Proxy-Authorization, Cookie, Set-Cookie, vstupný token, hlavičky označené ako tajné a bežné citlivé JSON kľúče sa maskujú pred zápisom. Každý hook môže doplniť názvy citlivých query a JSON polí. Tajnosť sa prenáša aj pri dosadzovaní: ak šablóna používa známu tajnú vstupnú hodnotu, celé výsledné pole sa v náhľade a histórii skryje, aj keď ide o cieľovú URL alebo telo. Vlastné telá môžu obsahovať ďalšie citlivé údaje, preto sa zachytávanie zapína vedome. Logy servera nevypisujú query reťazce, obsah hlavičiek ani telá.

Retencia je predvolene 7 dní a najviac 10 000 ukončených záznamov globálne, s nastaviteľnými hodnotami cez env. Bežiace záznamy sa neodstraňujú a ich počet je obmedzený globálnou kapacitou volaní. Čistenie prebieha periodicky v tom istom procese. Zmazanie hooku ponechá históriu do uplynutia retencie, ale zmaže jeho aktívnu konfiguráciu a tajomstvá. Testovacie volanie správcu je skutočné volanie a v histórii je jasne označené; neúspešný test nespustí automatický retry.

### Prihlásenie a uloženie dát

Pri úplne prvom štarte prázdnej databázy sa vytvorí admin z ADMIN_USERNAME a ADMIN_PASSWORD; ďalšie štarty jeho účet ani heslo neprepisujú. Ďalších používateľov admin vytvára cez web: jedinečné používateľské meno, heslo, rola admin alebo user a stav aktívny alebo neaktívny. Vie zmeniť heslo, rolu a pridelenie hookov; používateľ vie zmeniť vlastné heslo. Verejná registrácia ani emailová obnova hesla nie sú súčasťou prvej verzie. Heslá sa ukladajú ako Argon2id hashe. Prihlásenie používa serverové sessions s expiráciou 24 hodín, cookie HttpOnly a SameSite=Lax, CSRF ochranu pri zmenách a obmedzenie pokusov na 5 neúspechov za 15 minút podľa mena účtu a IP. Deaktivácia, zmena hesla alebo roly zruší existujúce sessions. Posledného aktívneho admina nemožno deaktivovať ani degradovať. Verejná APP_URL určuje Secure cookies pri HTTPS a URL zobrazované v editore; verejné HTTPS ukončuje reverzná proxy.

Používateľské oprávnenia sa kontrolujú na serveri pri každej operácii a v databázových filtroch histórie a agregácií. Nepridelený hook aj jeho volania sa bežnému používateľovi javia ako 404; zakázané administrátorské operácie vracajú 403. Parametre šablóny nesmú zmeniť pridelenie hooku ani jeho autentifikáciu. Verejné hooky sa naďalej autentifikujú vlastným tokenom, oddelene od účtov. Keď používateľ stratí alebo zabudne posledné admin heslo, lokálny príkaz v kontajneri reset-password prijme nové heslo zo štandardného vstupu a zruší sessions daného účtu.

SQLite databáza je v /data/hookrouty.sqlite3, používa WAL a verziované migrácie. Sieťové čakanie nedrží otvorenú databázovú transakciu. Aplikácia má jeden Uvicorn worker. Tabuľky: users, sessions, hooks, hook_members, executions, aggregate_counters a app_meta. Bežný používateľ môže vytvoriť hook a tým získa jeho pridelenie; ďalšie pridelenia mení iba admin. Pridelenie dáva právo hook upravovať, zmazať, testovať, meniť jeho token a prezerať históriu. Admin má prístup ku všetkým hookom. Odobratie pridelenia platí okamžite aj pre detail histórie a štatistiky. Zmazaný hook ponechá pôvodné pridelenia ako oprávnenia k zostávajúcej histórii do vypršania retencie; jeho token a aktívna konfigurácia sa odstránia. Používateľské účty sa spravujú deaktiváciou namiesto tvrdého mazania, aby zostali zachované odkazy v histórii. Hlavičky, telo a ďalšia štruktúrovaná konfigurácia sa môžu ukladať ako JSON stĺpce v SQLite; správca s JSON konfiguračnými súbormi na hoste nepracuje.

Tajné cieľové hlavičky sa šifrujú autentifikovaným šifrovaním pomocou povinného APP_SECRET_KEY z env; kľúč sa neukladá do databázy. Pre session a šifrovanie sa použijú oddelené odvodené kľúče. Zmena kľúča vyžaduje migráciu tajomstiev; v prvej verzii sa kľúč zálohuje a nemení bežnou úpravou konfigurácie. Ručne zadané tajomstvá v URL alebo tele nie sú automaticky rozpoznateľné; rozhranie uprednostní tajné hlavičky a výslovne umožní označiť celé nakonfigurované telo ako tajné. Také telo je tiež šifrované a nezachytáva sa v histórii. URL nesmie obsahovať userinfo; tajné URL query hodnoty sa konfigurujú ako samostatné tajné query polia s rovnakým šifrovaním a maskovaním.

## Kontrakty pre implementáciu

Tento dokument je samostatný podklad na implementáciu. Pracovný názov produktu je **Hookrouty**, názov Python balíka a repozitára `hookrouty`. Zmena finálneho obchodného názvu nemá meniť funkčné správanie. Predchádzajúci názov Webhook Router označoval tú istú aplikáciu.

### Oddelenie webu, API a hookov

| Prefix alebo cesta | Použitie |
| --- | --- |
| `/` | Presmerovanie na `/app/hooks`, prípadne na login |
| `/login` | Prihlásenie správcu alebo používateľa |
| `/app/*` | HTML rozhranie po prihlásení |
| `/api/*` | JSON API na správu so session a CSRF |
| `/hooks/*` | Verejné webhook endpointy s vlastným tokenom |
| `/healthz` | Healthcheck bez prihlasovania, iba stav ok alebo nedostupné |

Verejné hooky sa nesmú prekrývať s routami formulárov. Napríklad `GET /hooks/new` môže byť platným verejným hookom, kým formulár na vytvorenie hooku je `GET /app/hooks/new`. HTML stránky presmerujú neprihláseného používateľa na login; API vráti 401. GET, HEAD a ďalšie verejné hook metódy používajú samostatnú kontrolu tokenu, nikdy administračnú session ako náhradu za token.

### Kľúčové hodnoty konfigurácie hooku

| Pole | Typ a pravidlo |
| --- | --- |
| `name` | Povinný čistý text, 1 až 120 znakov |
| `description` | Text do 2 000 znakov, predvolene prázdny |
| `enabled` | Boolean, pri novom hooku predvolene false |
| `input_method` | Jedna z GET, POST, PUT, PATCH, DELETE, HEAD |
| `input_path` | Absolútna cesta pod `/hooks/`, napríklad `/hooks/deploy/:project` |
| `auth.mode` | `header` alebo `query`; vstupný token je vždy povinný |
| `auth.token` | Pri vytvorení alebo rotácii voliteľný vlastný token, inak sa vygeneruje; prázdny token sa odmietne |
| `target.method` | Jedna povolená metóda; môže byť iná než vstupná |
| `target.url` | HTTP alebo HTTPS, bez userinfo a fragmentu; host a port sú pevné |
| `target.query` | Usporiadané polia name, value, secret; názov je pevný, hodnota môže byť šablóna |
| `target.headers` | Polia name, value, secret; jedinečné názvy bez ohľadu na veľkosť písmen |
| `target.body.mode` | `none`, `passthrough`, `text`, `json` |
| `target.body.template` | Pri text string; pri json hodnota JSON; ostatné režimy null |
| `target.body.secret` | Boolean; celé tajné telo sa šifruje a nezachytáva do histórie |
| `timeout_seconds` | Celé číslo od 1 do 900, predvolene 60 |
| `capture_bodies` | Boolean, predvolene false |
| `secret_refs` | Doplňujúce tajné odkazy, napríklad `query.api_key` alebo `body.password` |

Premenné ciest majú jedinečné mená podľa `[A-Za-z_][A-Za-z0-9_]*`. Wildcard na viac segmentov sa v prvej verzii nepodporuje. Prázdne hodnoty parametrov ciest sa odmietnu. Vstup sa dekóduje presne raz, takže `%252F` ostane hodnotou `%2F` a nestane sa novým segmentom. Zakódované oddeľovače, NUL a neplatné percentové alebo UTF-8 sekvencie sa odmietnu ako 400.

Pevné query parametre v target.url sa zachovajú. target.query sa pridá v uvedenom poradí. Identifikátor riadka query/header po uložení slúži na bezpečnú úpravu rovnakého tajného poľa; nový riadok ho ešte nemá. Názvy HTTP hlavičiek musia byť platné HTTP field names; hodnoty nesmú obsahovať CR/LF. Vyhradené Host, Content-Length a hop-by-hop hlavičky sa pri ukladaní odmietnu namiesto tichého ignorovania.

### Príklad vytvorenia hooku

Nasledujúca požiadavka je pre prihláseného používateľa a vyžaduje CSRF token. Hlavičkový token v príklade je iba ukážková hodnota, nie existujúci credential.

```http
POST /api/hooks
Content-Type: application/json
X-CSRF-Token: <session-csrf-token>
```

```json
{
  "name": "Deploy application",
  "description": "Generic deployment API call",
  "enabled": true,
  "input_method": "POST",
  "input_path": "/hooks/deploy/:project/:environment",
  "auth": {"mode": "header"},
  "target": {
    "method": "POST",
    "url": "https://deployment.example.com/apps/{{path.project}}/deploy",
    "query": [
      {"name": "environment", "value": "{{path.environment}}", "secret": false}
    ],
    "headers": [
      {"name": "Authorization", "value": "Bearer example-only-replace-me", "secret": true}
    ],
    "body": {
      "mode": "json",
      "template": {"version": "{{query.version}}", "force": "{{body.force}}"},
      "secret": false
    }
  },
  "timeout_seconds": 60,
  "capture_bodies": false,
  "secret_refs": []
}
```

Odpoveď 201 má tvar `{"id":"<uuid>","token":"<new-token>","hook":{...}}`. `token` sa vráti iba pri vytvorení a rotácii. Kópia hooku vráti nový token a je vypnutá; vyžaduje novú vstupnú cestu. Ostatné odpovede používajú safe reprezentáciu hooku, bez hashu tokenu a bez plaintext hodnôt tajných polí.

Pri GET existujúceho tajného poľa sa vráti `value: null`, `secret: true` a `configured: true`. Pri PATCH sa použije `value_action: keep`, `replace` alebo `remove`; keep nevyžaduje hodnotu, replace vyžaduje novú hodnotu a remove pole odstráni. Rovnaké pravidlo sa použije na celé tajné telo. Text masky z UI sa nesmie uložiť ako nová hodnota.

### Príklad verejného volania

```bash
curl --fail-with-body \
  -X POST 'https://hooks.example.com/hooks/deploy/my-api/production?version=1.2.3' \
  -H 'X-Webhook-Token: REPLACE_WITH_GENERATED_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"force":true}'
```

Cieľ dostane POST na `https://deployment.example.com/apps/my-api/deploy?environment=production` s JSON `{"version":"1.2.3","force":true}` a nakonfigurovanou autorizáciou. Volajúci čaká na cieľovú odpoveď. Hodnoty z query sa považujú za stringy; body odkazy zachovajú JSON typ, keď tvoria celú hodnotu šablóny.

### Preview a manuálny test

Oba endpointy prijímajú rovnakú sadu vzorových dát:

```json
{
  "path": {"project": "my-api", "environment": "production"},
  "query": [["version", "1.2.3"]],
  "headers": [["Content-Type", "application/json"]],
  "body": "{\"force\":true}"
}
```

`body` je text reprezentujúci vzorové prijaté bajty v UTF-8. Prázdny body je prázdny string. Preview vráti maskovanú metódu, URL, hlavičky a telo bez vykonania sieťového volania a bez záznamu executions. Manuálny test vyžaduje prístup k danému hooku, vykoná rovnaký renderer a proxy engine a uloží `source=manual_test`. Vypnutý hook možno testovať v GUI; tým sa nezapína verejný endpoint. Verejné požiadavky nemôžu zvoliť ani zmeniť hodnotu source.

### Chybová odpoveď routera

```json
{
  "error": {
    "code": "UPSTREAM_TIMEOUT",
    "message": "Destination did not respond before the timeout."
  },
  "request_id": "16b41183-fd56-4b38-a80b-193b92bdc0e9"
}
```

Request ID sa generuje raz pri vstupe a je rovnaké v odpovedi, histórii aj bezpečnom serverovom logu. Klientom dodané ID sa na tento účel nepovažuje za dôveryhodné. Hlavička je `X-Hookrouty-Request-Id`. Cieľové 4xx alebo 5xx zostávajú pôvodnou cieľovou odpoveďou; tento formát sa používa pre lokálne chyby routera.

| Situácia | Status | Code |
| --- | --- | --- |
| Nesprávny vstup, chýbajúca premenná alebo neplatná šablóna | 400 | INVALID_INPUT alebo TEMPLATE_ERROR |
| Chýbajúci alebo nesprávny token | 401 | INVALID_TOKEN |
| Neexistujúci alebo vypnutý hook | 404 | HOOK_NOT_FOUND |
| Nepovolená vstupná metóda existujúcej route | 405 | METHOD_NOT_ALLOWED |
| Príliš veľké vstupné alebo zostavené telo | 413 | PAYLOAD_TOO_LARGE |
| Sieťová alebo TLS chyba cieľa | 502 | UPSTREAM_CONNECTION_ERROR |
| Príliš veľká cieľová odpoveď | 502 | UPSTREAM_RESPONSE_TOO_LARGE |
| Vyčerpaná kapacita volaní | 503 | CAPACITY_EXCEEDED |
| Vypršaný celkový sieťový deadline | 504 | UPSTREAM_TIMEOUT |

HEAD odpovede nemajú telo ani pri lokálnej chybe. Bežné validačné chyby modelov FastAPI sa prevedú na dokumentované 400; framework nesmie neúmyselne zaviesť odlišné 422. Pri zastavení aplikácie a chybách bez úspešne zapísanej histórie sa odpoveď ani log nesmú tváriť, že vzdialená akcia neprebehla.

### Spresnenia behu a monitoringu

- Režim manuálneho testu kontroluje používateľské oprávnenie; režim webhook kontroluje token a enabled. Obe vetvy vstupujú do rovnakého HTTP enginu, nie do skopírovanej implementácie.
- Pred sieťovým pokusom sa záznam running zapíše a potvrdí. Ak zápis zlyhá, cieľ sa nezavolá a router vráti 503 s bezpečným kódom STORAGE_UNAVAILABLE. Počas sieťového čakania nesmie ostať otvorená DB transakcia. Ak zlyhá zápis finálneho výsledku až po odpovedi cieľa, volajúci dostane skutočnú cieľovú odpoveď a hlavičku X-Hookrouty-History-Status: unavailable; sieťové volanie sa neopakuje. Chyba sa bezpečne zaloguje. Po obnovení DB najbližšie čistenie označí osirelé running záznamy bez aktívneho spracovania ako interrupted.
- Každé execution má boolean `attempted_target`. Odmietnutie, chýbajúca premenná a plná kapacita majú false; tesne pred pokusom o spojenie sa nastaví true. Aj timeout pri pripájaní sa považuje za cieľový pokus. Úspešnosť sa počíta nad ukončenými pokusmi; running, interrupted a client_disconnected sú samostatné stavy s neistým výsledkom.
- Pole `source` má iba hodnoty webhook a manual_test. Prehľad ich môže filtrovať. Historické časy sú UTC, web ich zobrazuje v lokálnom pásme prehliadača.
- Response body sa prenáša v pôvodných zakódovaných bajtoch z HTTPX aiter_raw; zachová sa Content-Encoding. Limit 8 MiB je limit prenesených bajtov. Komprimované telá sa v prvej verzii neukladajú do histórie, ani keď je capture zapnuté; zostanú metadáta a označenie encoded_body_omitted.
- Pri 204/304 a HEAD sa telo vynechá podľa HTTP pravidiel. Cieľové Set-Cookie ani hop-by-hop hlavičky sa neposielajú; HTTPX, HTTP server a middleware nesmú viesť druhú, odlišnú politiku.
- Retencia ponechá najviac 10 000 ukončených volaní, 7 dní; práve bežiacich môže byť navyše najviac nastavený globálny počet, predvolene 20. Globálne agregované počítadlá neznámych ciest sú viditeľné iba adminovi a mažú sa po 7 dňoch.
- Proxy vyvolá až jedno finálne ukončenie execution aj pri súbehu odpojenia klienta a návratu cieľa. Násilne prerušené running záznamy sa pri štarte zmenia na interrupted bez ďalšieho HTTP pokusu.
- Časové údaje deadline a duration_ms sa merajú monotónnymi hodinami; dátumy sú wall-clock UTC. Timeout počíta celý sieťový pokus vrátane pripojenia a načítania odpovede.
- Odpojenie klienta zruší lokálne čakanie; vracajúca sa vzdialená akcia sa nedá odvolať. Shutdown počká predvolene 30 sekúnd a potom zvyšné volania ukončí ako prerušené. Pri timeoutoch do 900 sekúnd sa do návodu doplní nastavenie dlhšieho ukončovacieho obdobia.
- Automatické HTTPX/Uvicorn access logy s celými URL sa vypnú. Logy aplikácie obsahujú ID volania, vzor route, stav a trvanie; nikdy plné query, raw body alebo nezačistený text HTTP výnimky.

## Konfigurácia a nasadenie

### Env premenné

| Premenná | Predvolené nastavenie | Význam |
| --- | --- | --- |
| APP_SECRET_KEY | Povinná | URL-safe Base64 kľúč obsahujúci 32 náhodných bajtov |
| ADMIN_USERNAME | admin | Meno prvého admina, iba pri prázdnej databáze |
| ADMIN_PASSWORD | Povinná pri prvej inicializácii | Heslo prvého admina; po vytvorení účtu sa dá z env odstrániť |
| APP_URL | http://localhost:8080 | Verejná základná URL bez koncového lomítka; určuje Secure cookies a zobrazené hook URL |
| DATA_DIR | /data | SQLite a interné aplikačné dáta |
| LOG_LEVEL | INFO | Bezpečné serverové logy |
| MAX_INFLIGHT_REQUESTS | 20 | Najviac súčasných cieľových volaní |
| MAX_REQUEST_BODY_BYTES | 1048576 | Limit vstupného tela |
| MAX_RENDERED_BODY_BYTES | 1048576 | Limit zostaveného tela |
| MAX_UPSTREAM_RESPONSE_BYTES | 8388608 | Limit prijatej cieľovej odpovede |
| HISTORY_CAPTURE_BYTES | 16384 | Najväčší uložený náhľad jedného tela |
| HISTORY_RETENTION_DAYS | 7 | Najvyšší vek histórie |
| HISTORY_MAX_RECORDS | 10000 | Najvyšší počet ukončených záznamov |
| SHUTDOWN_GRACE_SECONDS | 30 | Čakanie na dobehnutie volaní pri ukončení |

Env majú presné názvy uvedené v tabuľke, bez dodatočného prefixu. Všetky limity sú kladné celé čísla. Pri prekročení prihlasovacieho limitu sa vráti 429 s Retry-After; blokovanie zohľadňuje meno účtu aj IP samostatne. Odpoveď neprezrádza existenciu mena účtu. Zvýšenie limitov histórie nemení limity proxy a naopak. Timeout je vlastnosť hooku. Sessions, login rate limit a interval čistenia sú v prvej verzii hodnoty uvedené v špecifikácii, bez ďalších konfiguračných prepínačov.

Generovanie kľúča bez projektových závislostí:

```bash
python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

Kľúč sa uchová spolu so zálohou databázy. Aplikácia pri štarte s iným kľúčom odmietne obsluhu, namiesto tichého znefunkčnenia uložených tajomstiev.

### Lokálne spustenie pri implementácii

Po vytvorení projektu a uzamknutí závislostí:

```bash
uv sync --locked
uv run uvicorn hookrouty.app:create_app \
  --factory --host 0.0.0.0 --port 8080 --workers 1 --no-access-log
```

Pred spustením treba nastaviť APP_SECRET_KEY, pri prázdnej DB aj ADMIN_PASSWORD a pre lokálne spustenie DATA_DIR na zapisovateľný adresár. Generovanie `.env` ani jeho automatické načítanie nie je podmienkou; shell aj Compose môžu dodať env priamo.

### Compose pre nasadenie hotového image

HOOKROUTY_IMAGE je nasadzovací parameter pre Compose, nie aplikačná env premenná. Pri implementácii sa vytvorí lokálny image `hookrouty:local`; pri publikovaní sa použije konkrétny image a verzia z vybraného registra. Tento dokument netvrdí, že taký publikovaný image už existuje.

```yaml
services:
  hookrouty:
    image: ${HOOKROUTY_IMAGE:?Set the image and version to deploy}
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      APP_SECRET_KEY: ${APP_SECRET_KEY:?Set APP_SECRET_KEY}
      ADMIN_USERNAME: ${ADMIN_USERNAME:-admin}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-}
      APP_URL: ${APP_URL:-http://localhost:8080}
      DATA_DIR: /data
    volumes:
      - hookrouty-data:/data
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    stop_grace_period: 35s
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
      interval: 30s
      timeout: 3s
      retries: 3
      start_period: 10s

volumes:
  hookrouty-data:
```

Dockerfile nastaví neprivilegovaného používateľa, vlastníctvo /data a runtime príkaz Uvicorn vyššie. Pri lokálnom builde sa použije `docker build -t hookrouty:local .` a HOOKROUTY_IMAGE=hookrouty:local. Produkčný Compose potrebuje iba image; zostavovanie projektu na serveri nie je podmienkou. Dockhand je jedna z možností nasadenia, nie závislosť produktu.

Pri verejnom HTTPS nasadení APP_URL zodpovedá skutočnej URL. Reverzná proxy musí mať timeout dostatočný pre najdlhší timeout hooku. Pri dlhom graceful shutdown treba upraviť zároveň SHUTDOWN_GRACE_SECONDS aj stop_grace_period tak, aby Docker aplikáciu nezabil skôr.

### Obnova a prevádzka

- SQLite sa zálohuje konzistentným backup API alebo pri zastavenej aplikácii; počas behu sa nekopíruje iba súbor bez WAL. Súčasťou zálohy je APP_SECRET_KEY.
- Zabudnuté heslo admin obnoví lokálnym príkazom reset-password s heslom zo stdin. Reset neobnovuje už neaktívny účet a nevytvára nový účet; sessions daného účtu zruší.
- Po prvom štarte zmeny ADMIN_USERNAME a ADMIN_PASSWORD v env neupravujú používateľov. Používatelia sa spravujú cez web alebo explicitný lokálny recovery príkaz.
- Migrácie bežia pred začatím obsluhy. Neúspešná migrácia alebo nesprávny kľúč ukončí štart s bezpečnou správou. Upgrade sa overí testom nad databázou predchádzajúcej verzie.
- Healthcheck pri nedostupnej databáze vracia 503 a neprezrádza cestu, SQL chybu ani tajomstvá. Nezávisí od dostupnosti cieľových služieb.

## Súbory a zodpovednosti

Všetky uvedené súbory sa vytvoria v novom repozitári.

| Súbory | Zodpovednosť |
| --- | --- |
| `pyproject.toml`, `uv.lock` | Závislosti, testovanie a formátovanie |
| `src/hookrouty/__init__.py`, `app.py`, `config.py` | Vytvorenie aplikácie, lifespan, env konfigurácia |
| `src/hookrouty/models.py` | Spoločné validačné a dátové typy |
| `src/hookrouty/db.py`, `migrations/0001_initial.sql` | Spojenia, transakcie a migrácie |
| `src/hookrouty/security.py`, `auth.py` | Heslá, tokeny, šifrovanie, sessions a CSRF |
| `src/hookrouty/users.py`, `admin.py` | Účty, oprávnenia a lokálna obnova hesla |
| `src/hookrouty/hooks.py`, `routing.py` | CRUD hookov a párovanie vstupných ciest |
| `src/hookrouty/templating.py` | Dosadzovanie hodnôt a tvorba cieľového requestu |
| `src/hookrouty/history.py`, `redaction.py` | História, štatistiky, retencia a maskovanie |
| `src/hookrouty/proxy.py` | Deadline, HTTP volanie, limity a odpojenie klienta |
| `src/hookrouty/api.py`, `web.py` | JSON API na správu a HTML obsluha |
| `src/hookrouty/templates/{base,login,hooks,hook_edit,hook_test,executions,execution_detail,users,profile}.html` | HTML stránky |
| `src/hookrouty/static/app.css`, `app.js` | Vzhľad, editácia polí, náhľad a polling |
| `tests/conftest.py`, `test_app.py`, `test_auth.py`, `test_users.py`, `test_hooks.py`, `test_routing.py`, `test_templating.py`, `test_history.py`, `test_proxy.py`, `test_web.py` | Jednotkové a integračné overenie |
| `tests/e2e/test_user_journey.py`, `tests/container/test_smoke.py` | Prehliadač a Docker smoke test |
| `Dockerfile`, `compose.yaml`, `.dockerignore`, `.github/workflows/ci.yml`, `README.md` | Build, nasadenie, CI a návod |

## Dátový model a rozhrania

ID sú UUID uložené ako TEXT a časy sa ukladajú ako UTC ISO 8601 s koncovkou Z v jednotnom formáte. SQL hodnoty sa posielajú cez parametrizované queries; dynamické triedenie má whitelist stĺpcov. JSON konfigurácie sa validuje pred uložením. Tabuľka `schema_migrations` sleduje migrácie.

| Tabuľka | Základné polia |
| --- | --- |
| `users` | id, username, password_hash, role, active, auth_version, created_at |
| `sessions` | token_digest, user_id, auth_version, csrf_token, expires_at |
| `hooks` | id, name, enabled, input_method, input_path, token_hash, config_json, created_by, deleted_at |
| `hook_members` | hook_id, user_id; jedinečná dvojica |
| `executions` | request_id, hook_id, hook_name_snapshot, source, attempted_target, started_at, finished_at, outcome, upstream_status, duration_ms, safe_snapshot |
| `aggregate_counters` | bucket_start, kind, count; globálne počítadlá neznámych ciest |
| `app_meta` | key, value; kontrolná hodnota kľúča a interné nastavenia aplikácie |

Vymazanie hooku ponechá minimálny tombstone a pridelenia kvôli prístupu k histórii; odstráni jeho token a aktívnu konfiguráciu vrátane tajomstiev. Účty sa deaktivujú. Používateľské meno je jedinečné bez ohľadu na veľkosť písmen, 3 až 64 ASCII znakov z množiny písmen, číslic, bodky, pomlčky a podčiarkovníka.

Spoločné typy v `models.py`: `Principal(id, username, role)`, `HookSpec`, `HookRecord(id, spec)`, `FieldSpec(name, value, secret)`, `TemplateContext(path, query, headers, body, secret_refs)`, `RenderedRequest(method, url, headers, body, secret_fields)`, `ProxyReply(status_code, headers, body)`, `ExecutionFilter`, `Stats` a `Page[T]`. `JsonValue` znamená hodnotu z JSON dátového modelu. `RouterError(status_code, code, public_message)` prenáša iba bezpečnú chybu. Pomocné typy CreatedHook, CompiledRoute, RouteMatch, RouteConflict, IncomingRequest, CallSnapshot, SafeSnapshot, UserPatch, UserRead a ExecutionRead definujú úlohy, ktoré ich produkujú, a verejne ich exportujú. Konkrétne polia HookSpec zodpovedajú nastaveniam hooku v špecifikácii.

API na správu je pod `/api/`. Session používa cookie `hookrouty_session`; CSRF sa posiela cez `X-CSRF-Token` alebo skryté formulárové pole. `GET /api/session` poskytne CSRF token a aktuálneho používateľa alebo anonymnú session. Po úspešnom prihlásení sa ID session aj CSRF token zmenia. Anonymná session má životnosť 15 minút.

| Endpoint | Účel |
| --- | --- |
| `POST /api/login`, `POST /api/logout`, `POST /api/me/password` | Prihlásenie, odhlásenie, vlastné heslo |
| `GET/POST /api/users`, `PATCH /api/users/{id}`, `POST /api/users/{id}/password` | Admin správa účtov |
| `GET/POST /api/hooks`, `GET/PATCH/DELETE /api/hooks/{id}` | CRUD s kontrolou pridelenia |
| `PUT /api/hooks/{id}/members` | Admin mení pridelených používateľov |
| `POST /api/hooks/{id}/duplicate`, `POST /api/hooks/{id}/rotate-token` | Kópia s novou cestou a tokenom, rotácia tokenu |
| `POST /api/hooks/{id}/preview`, `POST /api/hooks/{id}/test` | Bezpečný náhľad a označené skutočné volanie |
| `GET /api/executions`, `GET /api/executions/{request_id}`, `GET /api/stats` | Filtrovaná história a štatistiky |
| `/hooks/{hook_path}` | Verejné vykonanie hooku; GET, POST, PUT, PATCH, DELETE, HEAD |
| `GET /healthz` | Aplikácia a databáza |

API vracia 201 pri vytvorení, 204 pri zmazaní alebo odhlásení, 400 pri validačnej chybe konfigurácie a 409 pri konflikte mena, route alebo ochrane posledného admina. Neprihlásený prístup do API na správu vracia 401; nepridelený hook 404 a admin operácia vykonaná bežným používateľom 403. Verejné hooky majú chyby presne podľa špecifikácie.

## Zameranie kontroly

1. **Izolácia používateľov:** nepridelený hook sa nesmie objaviť ani v detaile volania, teste, duplikovaní alebo agregovaných štatistikách. Overí úloha 3 a integrácia v úlohe 8.
2. **Nejednoznačné a zakódované cesty:** pevná cesta vyhrá nad parametrom; rovnaké vzory sa odmietnu a zakódované oddeľovače nezmenia route. Overí úloha 4.
3. **Tajomstvá prenesené šablónou:** tajná hodnota nesmie uniknúť v cieľovej URL, chybe, náhľade, logoch ani skrátenom tele. Overia úlohy 5 a 6.
4. **Neistý výsledok vzdialenej akcie:** timeout, odpojenie a reštart nesmú viesť k automatickému opakovaniu. Overí úloha 7.
5. **Prepísanie HTTP odpovede:** kompresia, HEAD, 204/304, binárne dáta a hop-by-hop hlavičky musia zachovať korektnú odpoveď volajúcemu. Overí úloha 7.

## Implementačné úlohy

Každá úloha končí funkčným, samostatne overiteľným výsledkom a jedným súvislým commitom. Ukážky testov sú konkrétne kontrolné body; povinné doplňujúce scenáre sú vypísané pri príslušnej úlohe. Fixtures patria do `tests/conftest.py`: `db` je dočasná SQLite, `settings` testovacia konfigurácia a `client` HTTP klient nad aplikáciou bez otvorenia portu. `admin_client`, `member_client` a `outsider_client` sa doplnia pri úlohách 2 a 3 a posielajú správny CSRF token.

### Úloha 1 Spustiteľný základ a databáza

**Súbory:** `pyproject.toml`, `uv.lock`, `src/hookrouty/{__init__,app,config,models,db}.py`, `migrations/0001_initial.sql`, `tests/{conftest,test_app}.py`.

**Rozhrania:** `create_app(settings: Settings | None = None) -> FastAPI`; `await Database.open(path: Path) -> Database`; `await Database.migrate() -> None`; `Database.transaction() -> AsyncContextManager[Connection]`. Nastavenia definujú všetky env hodnoty z globálnych hraníc, APP_URL, DATA_DIR, bootstrap admina a kľúč.

- [ ] Napísať `test_health_uses_persistent_database` a testy, že chýbajúci alebo neplatný APP_SECRET_KEY zabráni štartu, migrácie sú opakovateľné a schéma obsahuje všetkých sedem aplikačných tabuliek.

```python
async def test_health_uses_persistent_database(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

- [ ] Spustiť `uv run pytest tests/test_app.py -q`; prvý beh musí zlyhať pre chýbajúcu aplikáciu alebo schému.
- [ ] Implementovať rozhrania, SQLite WAL, cudzie kľúče, verziované migrácie a lifespan. Zostaviť a uzamknúť kompatibilné závislosti cez `uv lock`; aplikačné nastavenia validovať pri štarte.
- [ ] Spustiť `uv run pytest tests/test_app.py -q` a `uv run ruff check .`; výsledok musí byť PASS a bez lint chýb.
- [ ] Commit: `feat: add application foundation and sqlite schema`.

### Úloha 2 Prihlasovanie a bezpečné tajomstvá

**Súbory:** `security.py`, `auth.py`, `admin.py`, `tests/test_auth.py`; upraviť `app.py` a `tests/conftest.py`.

**Vstupy:** Database a Settings z úlohy 1. **Výstupy:** `hash_password(password: str) -> str`, `verify_password(password: str, digest: str) -> bool`, `token_digest(token: str) -> str`; `SecretBox.encrypt(value: str) -> str`, `SecretBox.decrypt(value: str) -> str`; `await bootstrap_admin(db: Database, settings: Settings) -> None`; `await current_principal(request: Request) -> Principal`; `require_csrf(request: Request) -> None`.

- [ ] Napísať testy login/logout, rotácie session, CSRF, exspirácie 24 hodín, 5 neúspechov za 15 minút, šifrovania a odmietnutia nesprávneho kľúča. Bootstrap test po prvom štarte zmení heslo cez službu, znovu zavolá bootstrap s pôvodnými env a overí, že nové heslo zostalo platné.

```python
def test_password_is_not_stored_in_plaintext():
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", digest)
    assert not verify_password("wrong password", digest)
```

- [ ] Spustiť `uv run pytest tests/test_auth.py -q`; očakáva sa zlyhanie nových testov.
- [ ] Implementovať Argon2id, náhodné 256-bitové session a webhook tokeny, oddelené odvodené kľúče pre hashovanie sessions a šifrovanie cieľových polí. Kľúčová kontrolná hodnota v databáze zabráni tichému štartu s iným APP_SECRET_KEY. Bootstrap sa vykoná v transakcii iba bez existujúcich účtov. Pridať reset hesla cez `python -m hookrouty.admin reset-password --username admin --password-stdin`, ktorý číta heslo zo stdin a zruší sessions.
- [ ] Spustiť `uv run pytest tests/test_auth.py tests/test_app.py -q`; všetko PASS. Overiť, že logy neobsahujú heslá ani tokeny a reset hesla ich nedáva do argumentov procesu.
- [ ] Commit: `feat: add local authentication and encrypted secrets`.

### Úloha 3 Používatelia a prístup k hookom

**Súbory:** `users.py`, `tests/test_users.py`; upraviť `auth.py`, `api.py`, `tests/conftest.py`.

**Vstupy:** Principal a sessions z úlohy 2. **Výstupy:** `await require_admin(principal: Principal) -> None`; `await require_hook_access(db: Database, principal: Principal, hook_id: UUID) -> None`; `await set_hook_members(db: Database, actor: Principal, hook_id: UUID, user_ids: list[UUID]) -> None`; `await update_user(db: Database, actor: Principal, user_id: UUID, patch: UserPatch) -> UserRead`.

- [ ] Napísať testy používateľských endpointov: jedinečné meno bez ohľadu na veľkosť písmen, admin a user, pridelenie a okamžité odobratie prístupu, zákaz samostatnej eskalácie roly a zrušenie sessions po deaktivácii, zmene roly alebo hesla.

```python
async def test_user_cannot_list_accounts(member_client):
    response = await member_client.get("/api/users")
    assert response.status_code == 403
```

- [ ] Spustiť `uv run pytest tests/test_users.py -q`; nové testy musia zlyhať.
- [ ] Implementovať správu účtov a SQL filtre oprávnení. V jednej transakcii chrániť posledného aktívneho admina pred deaktiváciou aj degradáciou. Históriu filtrovať cez hook_members, vrátane tombstone hookov; samotné skrytie tlačidiel vo webovom rozhraní nestačí.
- [ ] Spustiť `uv run pytest tests/test_auth.py tests/test_users.py -q`; PASS aj pri súbežných pokusoch degradovať posledných dvoch adminov. Po vytvorení endpointov v úlohách 4, 6 a 8 doplniť testy neprideleného detailu, testu, duplikovania a agregácií s očakávanou 404 alebo nulovým zahrnutím do štatistík.
- [ ] Commit: `feat: add user management and per-hook access`.

### Úloha 4 Správa hookov a parametrizované routovanie

**Súbory:** `hooks.py`, `routing.py`, `tests/test_hooks.py`, `tests/test_routing.py`; upraviť `models.py` a `api.py`.

**Vstupy:** Database, Principal, kontrola prístupu a token_digest. **Výstupy:** `await create_hook(db: Database, actor: Principal, spec: HookSpec) -> CreatedHook`; `compile_route(method: str, pattern: str) -> CompiledRoute`; `validate_route_set(routes: list[CompiledRoute]) -> None`; `resolve_route(routes: list[CompiledRoute], method: str, raw_path: bytes) -> RouteMatch`; `await verify_hook_token(hook: HookRecord, request: Request) -> None`. CreatedHook obsahuje HookRecord a token zobraziteľný iba raz; RouteMatch obsahuje hook_id a path parametre. Konflikt vyvolá RouteConflict.

- [ ] Napísať CRUD testy, rotáciu tokenu, vytvorenie a pridelenie autorovi, duplikáciu s novým tokenom a vypnutím, tombstone pri zmazaní. Pri routovaní testovať pevnú cestu pred parametrom, kolízie odlišné iba názvami parametrov, presnú metódu a koncové lomítko.

```python
def test_equal_shape_routes_are_rejected():
    existing = compile_route("POST", "/hooks/deploy/:project")
    with pytest.raises(RouteConflict):
        validate_route_set([existing, compile_route("POST", "/hooks/deploy/:name")])
```

- [ ] Spustiť `uv run pytest tests/test_hooks.py tests/test_routing.py -q`; nové scenáre musia zlyhať.
- [ ] Implementovať CRUD, autentifikáciu a dynamický dispatch pod `/hooks/`. Porovnávať masku segmentov zľava: pevný segment = 1 a parameter = 0; vyššia maska má prednosť. Vzory s rovnakou maskou a prienikom odmietnuť. Z raw_path odmietnuť zakódované oddeľovače `/`, spätné lomítko a NUL; dekódovať ostatné hodnoty presne raz. Nevytvárať fallback na iný hook pri neplatnom tokene. SecretEdit má akcie keep, replace a remove; UI maska nie je nová hodnota tajomstva.
- [ ] Spustiť testy vrátane `%2F`, `%252F`, Unicode a prázdneho segmentu, 401/404/405, zakázaného testu neprideleného hooku a zachovania tajnej hlavičky pri úprave. `uv run pytest tests/test_hooks.py tests/test_routing.py tests/test_users.py -q` musí prejsť.
- [ ] Commit: `feat: add hook configuration and parameter routing`.

### Úloha 5 Šablóny a zostavenie cieľovej požiadavky

**Súbory:** `templating.py`, `tests/test_templating.py`; doplniť `models.py`, `hooks.py` a preview endpoint v `api.py`.

**Vstupy:** HookSpec a prijaté dáta. **Výstupy:** `build_context(request: IncomingRequest, path: dict[str, str], secret_refs: set[str]) -> TemplateContext`; `render_request(spec: HookSpec, context: TemplateContext, original_body: bytes, content_type: str | None) -> RenderedRequest`. IncomingRequest obsahuje request_id: UUID, method: str, raw_path: bytes, query: list[tuple[str, str]], headers: list[tuple[str, str]] a body: bytes. request_id vznikne pri vstupe a History ho znovu negeneruje. `preview` používa rovnaký renderer ako skutočné vykonanie.

- [ ] Napísať testy namespace path/query/headers/body, case-insensitive hlavičiek, prvej hodnoty duplicitného query, chýbajúcej hodnoty, JSON typu a správneho escapovania.

```python
def test_json_template_preserves_boolean_and_escapes_text(context):
    context.body = {"enabled": True, "name": 'a"b'}
    rendered = render_json({"enabled": "{{body.enabled}}", "name": "{{body.name}}"}, context)
    assert json.loads(rendered) == {"enabled": True, "name": 'a"b'}
```

- [ ] Spustiť `uv run pytest tests/test_templating.py -q`; nové testy musia zlyhať.
- [ ] Implementovať vlastný obmedzený parser odkazov bez eval a bez vykonávania Jinja výrazov. `render_json(template: JsonValue, context: TemplateContext) -> bytes` zachová typ pri samostatnom odkaze a escapuje vložený text. URL hodnoty zakódovať podľa kontextu, zakázať CR/LF v hlavičkách a templating schémy/hosta/portu. Existujúce pevné query z target_url sa zachovajú a nakonfigurované query polia sa pridajú v poradí. Správne spracovať žiadne, pôvodné, textové a JSON telo; pri GET/HEAD zakázať cieľové telo.
- [ ] Spustiť testy vrátane vloženia objektu do textu s chybou 400, nezmenených pôvodných bajtov, pokusu vykonať kód a zmeny hosta vstupným parametrom. Propagovať secret_fields pre tajné vstupy dosadené do URL, hlavičiek alebo tela. `uv run pytest tests/test_templating.py tests/test_hooks.py -q` musí prejsť.
- [ ] Commit: `feat: add safe request templates and preview`.

### Úloha 6 História a monitoring

**Súbory:** `history.py`, `redaction.py`, `tests/test_history.py`; doplniť `api.py`, `app.py`.

**Vstupy:** RenderedRequest, secret_fields, Principal a Database. **Výstupy:** `redact_snapshot(snapshot: CallSnapshot, secret_refs: set[str], capture_bodies: bool, limit: int = 16384) -> SafeSnapshot`; `await History.start(request_id: UUID, hook: HookRecord, snapshot: SafeSnapshot, source: str) -> None`; `await History.finish(request_id: UUID, outcome: str, status: int | None, duration_ms: float, snapshot: SafeSnapshot) -> None`; `await History.list(principal: Principal, filters: ExecutionFilter) -> Page[ExecutionRead]`; `await History.stats(principal: Principal, since: datetime) -> Stats`; `await History.prune(now: datetime) -> None`.

- [ ] Napísať testy priebehu running až výsledok, filtrov a stránkovania, posledných 24 hodín a 7 dní, povolenej histórie zmazaného hooku a oddelenia lokálnych odmietnutí od úspešnosti cieľových pokusov.

```python
async def test_unassigned_executions_are_not_visible(history, outsider):
    result = await history.list(outsider, ExecutionFilter())
    assert result.total == 0
    assert result.items == []
```

- [ ] Spustiť `uv run pytest tests/test_history.py -q`; nové testy musia zlyhať.
- [ ] Implementovať bezpečný snapshot, maskovanie pred zápisom a až potom skracovanie. Celé pole odvodené z tajomstva skryť; binárne a vypnuté telá neukladať. Pri orezaní JSON najskôr vykonať maskovanie nad úplným vstupom a až potom vytvoriť textový náhľad. Do SafeSnapshot nesmú preniknúť raw exception, autentifikačný token ani nezačistená URL. Implementovať čistenie každých 5 minút pri súčasnom limite 7 dní a 10 000 ukončených záznamov; bežiace záznamy neodstraňovať. Neznáme cesty agregovať po denných UTC bucketoch a čistiť po 7 dňoch.
- [ ] Spustiť testy s injektovaným časom, tajomstvom za hranicou 16 KiB a tajomstvom dosadeným do URL; skontrolovať priamo obsah SQLite aj logy. Štatistiky nepočítajú running/interrupted/client_disconnected do ukončených pokusov; timeout a sieťové chyby po pokuse sú neúspech. `uv run pytest tests/test_history.py tests/test_users.py -q` musí prejsť.
- [ ] Commit: `feat: add scoped webhook history and monitoring`.

### Úloha 7 Synchrónne vykonanie HTTP volania

**Súbory:** `proxy.py`, `tests/test_proxy.py`; doplniť `app.py`, `api.py` a verejný dispatch.

**Vstupy:** RouteMatch, HookRecord, render_request, HTTPX AsyncClient a History. **Výstupy:** `await ProxyService.execute(hook: HookRecord, incoming: IncomingRequest, disconnect: Awaitable[None] | None, source: str = 'webhook') -> ProxyReply`; `await ProxyService.close(grace_seconds: int) -> None`. Hodnoty source sú webhook a manual_test. Verejná obsluha prevedie RouterError na bezpečný JSON a pridá X-Hookrouty-Request-Id.

- [ ] Napísať integračné testy proti riadenému lokálnemu cieľu: prijatá metóda, URL, hlavičky a telo, statusy 200/302/400/500, deadline, TLS/sieťová chyba a jeden jediný pokus.

```python
async def test_timeout_does_not_retry(proxy_client, slow_upstream):
    response = await proxy_client.post("/hooks/slow", headers={"X-Webhook-Token": "test-token"})
    assert response.status_code == 504
    assert slow_upstream.request_count == 1
    assert response.headers["X-Hookrouty-Request-Id"]
```

- [ ] Spustiť `uv run pytest tests/test_proxy.py -q`; nové scenáre musia zlyhať.
- [ ] Implementovať zdieľaný klient s follow_redirects=False, trust_env=False, timeout=None, overovaním TLS a nulovým retry. Deadline v asyncio.timeout pokrýva celý sieťový pokus. Kapacitný slot získať bez čakania a uvoľniť vo finally; po získaní slotu znovu overiť, že hook nie je zmazaný, a pre source=webhook aj enabled. Pre source=manual_test už musí byť overené používateľské oprávnenie a verejný enabled sa nevyžaduje. Vstup, zostavené telo a odpoveď čítať s limitom; kontrolovať skutočné bajty aj bez Content-Length. Cieľovú odpoveď čítať cez aiter_raw, zachovať jej Content-Encoding a kontrolovať limit 8 MiB nad prijatými bajtmi; Content-Length vypočítať znova. Odstrániť hop-by-hop hlavičky vrátane položiek v Connection. HEAD/204/304 vracať bez tela. Serverové cookies nepreposielať. Pretekanie odpovede cieľa a odpojenia klienta ukončiť jedným finálnym záznamom; po odpojení nerobiť retry. Komprimované telo histórie sa v prvej verzii nezachytáva, zostávajú metadáta; táto voľba sa uvedie pri prepínači zachytávania.
- [ ] Spustiť testy kompresie, binárnych dát, HEAD/204/304, presmerovania bez druhého requestu, všetkých limitov, 21. súbežného volania s 503 a odpojenia klienta. Simulovať pád po uložení running a pri štarte označiť interrupted bez opakovania. Skontrolovať nepresakovanie secretov v každej lokálnej chybe, jeden request_id v celom priebehu, zlyhanie DB pred volaním bez HTTP pokusu a zlyhanie finálneho zápisu po odpovedi cieľa bez retry. `uv run pytest tests/test_proxy.py tests/test_history.py tests/test_templating.py -q` musí prejsť.
- [ ] Commit: `feat: execute synchronous webhook proxy requests`.

### Úloha 8 Webové rozhranie

**Súbory:** `web.py`, všetky uvedené templates, `static/app.css`, `static/app.js`, `tests/test_web.py`, `tests/e2e/test_user_journey.py`; dokončiť `api.py`.

**Vstupy:** Existujúce API a služby úloh 2 až 7. **Výstupy:** HTML stránky `/login`, `/app/hooks`, `/app/hooks/new`, `/app/hooks/{id}/edit`, `/app/hooks/{id}/test`, `/app/executions`, `/app/executions/{id}`, `/app/users` a `/app/profile`. Web a API používajú tie isté autorizačné kontroly a služby.

- [ ] Napísať testy prihlásenia a používateľského priechodu: admin vytvorí účet, používateľ vytvorí parametrizovaný hook, pozrie maskovaný náhľad, vykoná test a nájde výsledok v histórii. Pridať celý priechod odobratia pridelenia a súčasnú existenciu formulára /app/hooks/new a verejného GET /hooks/new.

```python
async def test_admin_users_page_is_forbidden_to_member(member_client):
    response = await member_client.get("/app/users")
    assert response.status_code == 403
```

- [ ] Spustiť `uv run pytest tests/test_web.py -q`; testy nových stránok musia zlyhať.
- [ ] Implementovať jednoduché stránky s tabuľkami a formulármi. Editor má vstupnú metódu a cestu, token, cieľ, zoznam query a hlavičiek, režim tela, timeout a zachytávanie tiel. Preview nesmie zavolať cieľ; Test zreteľne vykonáva skutočné volanie a uloží source=manual_test. Monitoring sa obnovuje každých 5 sekúnd a polling sa pozastaví pri skrytej karte. Tajomstvá majú akcie ponechať, nahradiť a odstrániť. Autorov aj cudzie dáta vždy escapovať; odpovede cieľa sa zobrazujú ako text.
- [ ] Po `uv sync --locked` pripraviť Chromium cez `uv run playwright install chromium` (v CI `uv run playwright install --with-deps chromium`). Spustiť `uv run pytest tests/test_web.py -q` a `uv run pytest tests/e2e/test_user_journey.py -q`. V Playwright overiť aj úzku šírku 390 px, desktop 1440 px, klávesnicové formuláre, chybové a prázdne stavy a nefunkčnosť uloženého XSS v mene hooku alebo odpovedi. Doplniť chýbajúce testy izolácie podľa úlohy 3.
- [ ] Commit: `feat: add webhook management and monitoring ui`.

### Úloha 9 Docker a odovzdanie

**Súbory:** `Dockerfile`, `compose.yaml`, `.dockerignore`, `.github/workflows/ci.yml`, `README.md`, `tests/container/test_smoke.py`; doplniť balenie templates a static súborov v `pyproject.toml`.

**Vstupy:** Dokončená aplikácia. **Výstupy:** Image pre amd64/arm64, jeden vložiteľný Compose, návod a automatické overenie. Konkrétne meno registra a image sa určí podľa repozitára pri implementácii; tento plán žiadnu publikáciu nepovoľuje.

- [ ] Napísať smoke test, ktorý spustí kontajner s volume a env, vytvorí účet aj hook cez aplikáciu, zavolá lokálny testovací cieľ, reštartuje kontajner a overí zachovanie účtu, hooku aj histórie.

```python
def test_container_preserves_configuration_after_restart(running_container):
    hook_id = running_container.create_hook()
    running_container.restart()
    assert running_container.get_hook(hook_id)["id"] == hook_id
```

- [ ] Spustiť `uv run pytest tests/container/test_smoke.py -q`; pred dokončením Docker balenia musí test zlyhať.
- [ ] Vytvoriť neprivilegovaný runtime image s jedným workerom, `/data`, healthcheckom a graceful shutdown. Compose má image, port 8080, named volume a env; žiadny bind mount na lokálny konfiguračný súbor. Predvolené ukončovacie obdobie je 30 sekúnd, upraviteľné pre dlhé volania. Návod obsahuje generovanie APP_SECRET_KEY, prvý admin účet, APP_URL za HTTPS proxy, zálohu SQLite spolu s kľúčom, obnovu hesla, retenciu, limity a generický príklad použitia. Build test používa lokálny image tag; produkčný image sa publikuje až samostatne autorizovaným krokom.
- [ ] Najskôr zostaviť `docker build -t hookrouty:local .`. Potom spustiť `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest tests -q` a `docker compose config --quiet` s testovacími env vrátane HOOKROUTY_IMAGE=hookrouty:local. Buildx build pre linux/amd64 a linux/arm64 musí prejsť; runtime smoke test spustiť pre dostupnú architektúru a v CI pre obe. Prejsť healthcheck, perzistenciu, chýbajúci bootstrap password v prázdnej DB a štart existujúcej DB bez bootstrap hesla.
- [ ] Commit: `build: package webhook router for docker deployment`.

## Hotový výsledok

Implementácia je pripravená na odovzdanie, keď všetkých deväť úloh prejde svojím overením, používateľský priechod funguje v prehliadači a kontajner zachová konfiguráciu vo volume po reštarte. Admin spravuje účty a priradenia; bežný používateľ spravuje iba svoje pridelené hooky. Reálne volanie parametrizovaného hooku vykoná presne jednu nastavenú HTTP požiadavku, vráti jej odpoveď a uloží bezpečný záznam monitoringu.

| Požiadavka | Úlohy, ktoré ju dodajú |
| --- | --- |
| Python a jednoduchá prevádzka | 1, 9 |
| Používatelia, prihlasovanie a pridelenie hookov | 2, 3, 8 |
| Viac hookov a tokeny | 4, 8 |
| Parametrizovaná vstupná URL | 4 |
| Konfigurovateľné univerzálne HTTP volanie | 5, 7 |
| Čakanie na cieľovú odpoveď | 7 |
| Monitoring a história s oprávneniami | 3, 6, 8 |
| Správa cez web bez súborov na hoste | 8, 9 |

## Konečné akceptačné kritériá

- [ ] Projekt sa spustí z jedného image a Compose, bez povinných súborov na hoste mimo dátového volume.
- [ ] Prvý admin sa inicializuje iba raz; reštart neprepíše jeho heslo ani ďalších používateľov.
- [ ] Admin vytvára účty, mení roly a prideľuje hooky; posledný aktívny admin sa nedá odstrániť z tejto roly.
- [ ] Bežný používateľ vidí len pridelené hooky, ich volania, testy a štatistiky. Odobratie prístupu platí okamžite.
- [ ] Zmeny hesla, deaktivácia a zmena roly zrušia existujúce sessions. CSRF platí pre všetky zmeny v administrácii.
- [ ] Formuláre pod /app nekolidujú s ľubovoľným povoleným hookom pod /hooks.
- [ ] Kolízie ciest, nesprávna metóda, neplatný token a nesprávne zakódovaná cesta majú určený výsledok.
- [ ] Parametre z path, query, headers a JSON body sa bezpečne dosadia do konfigurácie cieľa.
- [ ] JSON šablóna zachová typ samostatného odkazu a správne escapuje hodnoty vložené do textu.
- [ ] Renderovanie a preview nespúšťajú kód a nemenia schému, host ani port cieľa.
- [ ] Verejné volanie čaká na výsledok, má jeden HTTP pokus a nepreposiela vstupnú autorizáciu automaticky.
- [ ] Timeout, odpojenie, reštart ani presmerovanie nespustia automatické opakovanie cieľovej akcie.
- [ ] Limity fungujú aj bez Content-Length; HTTP odpoveď má konzistentné bajty a hlavičky pri gzip, binárnych dátach, HEAD a 204/304.
- [ ] Preview je iba náhľad; manuálny test vykoná skutočné volanie a má vlastné označenie v histórii.
- [ ] História obsahuje totožné request_id ako odpoveď a bezpečný log; retencia aj maximálny počet záznamov sa uplatňujú.
- [ ] Tajné hodnoty sa maskujú pred zápisom a pred skracovaním vrátane hodnôt prenesených šablónami.
- [ ] Pri zlyhaní zápisu záznamu pred odoslaním sa cieľ nezavolá; pri páde po odoslaní výsledok zostane explicitne neistý.
- [ ] Dáta a účty prežijú reštart kontajnera. Záloha a reset admin hesla majú overený postup.
- [ ] Prejdú jednotkové, integračné, prehliadačové a kontajnerové kontroly úloh; produkčný image funguje na amd64 a arm64.

## Odovzdanie implementátorovi

Implementuj produkt podľa tohto súboru v uvedenom poradí deviatich úloh. Tento súbor je normatívny aj pri rozdieloch oproti starším návrhom: verejné hooky sú pod /hooks, HTML správa pod /app, request ID hlavička je X-Hookrouty-Request-Id a balík sa volá hookrouty. Pri vypracovaní kódu doplň presné typové definície a schémy, ktoré zodpovedajú opísaným kontraktom; nevytváraj druhé, odlišné API medzi webom a službami.

Každú úlohu ukonči jej testami a súvislým commitom. Zaškrtnutie kroku znamená jeho skutočné dokončenie, nie iba vytvorenie súboru. Bez potreby zmeny zadania zvoľ jednoduchšie riešenie. Presné kompatibilné verzie závislostí a výsledný Docker image tag stanov pri implementácii, uzamkni ich a zaznamenaj v README. Tento dokument sám nepublikuje image ani nenasadzuje službu.

Na odovzdanie patria zdrojové kódy, uv.lock, databázové migrácie, testy, Dockerfile, jeden produkčný compose.yaml, README a tento dokument so stavom úloh. README má zahŕňať prvé spustenie, správu účtov, vytvorenie parametrizovaného hooku, testovanie, monitoring, env, zálohu, obnovu a upgrade.
