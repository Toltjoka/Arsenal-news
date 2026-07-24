# Arsenal Transfer News Bot

Hämtar RSS-nyheter, filtrerar fram bara spelar-transfers (in/out,
rykte eller bekräftat) för Arsenal, och sparar i Supabase. Skador filtreras bort.

## Setup

1. Skapa ett nytt repo på GitHub och lägg in dessa filer (behåll mappstrukturen).
2. I repot: **Settings → Secrets and variables → Actions → New repository secret**
   - `SUPABASE_URL` = `https://fajtxhxwvnuhfkytdcqi.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = din service role key (Supabase dashboard → Project Settings → API)
3. Klart. Action körs automatiskt var 30:e minut, eller kör manuellt via
   **Actions → Arsenal Transfer News → Run workflow**.

## Tabellen

`public.arsenal_transfer_news` i Supabase-projektet **Toltjoka's Project**:

| kolumn | innehåll |
|---|---|
| player_name | grov gissning från rubriken |
| direction | `in` eller `out` |
| status | `rumor` eller `confirmed` |
| headline | originalrubrik |
| source_name | tidning/källa |
| source_url | unik — används för att undvika dubbletter |
| published_at | publiceringstid om tillgänglig |

## Justera källor / filter

Redigera listorna `FEEDS`, `TRANSFER_RE`, `INJURY_RE` och `CONFIRMED_RE`
i `scripts/fetch_arsenal_news.py` om du vill lägga till fler källor
eller finjustera vad som räknas som transfer vs. skada.

## Nästa steg (frivilligt)

- En liten webbsida eller Slack/Discord-webhook som läser tabellen och
  visar senaste 20 raderna.
- Dagligt mejl-sammandrag via en till scheduled Action.
