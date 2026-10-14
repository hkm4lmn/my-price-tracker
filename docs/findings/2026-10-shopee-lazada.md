# Shopee and Lazada feasibility test

**Date:** 2026-10-15
**Question:** Can the tracker read product prices from Shopee and Lazada without logging in, without getting around anti-bot measures, and within each site's terms?
**Method:** Plain HTTP requests (curl) from a home connection in Malaysia, with a normal browser user agent, no cookies and no login. Not yet tested from a cloud server, which usually does worse.

## Shopee: do not build

| Check | Result |
|---|---|
| robots.txt | Product pages are not disallowed for `User-Agent: *` (`Crawl-delay: 1`). |
| Terms of Service | Could not be read without JavaScript, so it is not verified here. |
| Product page | `https://shopee.com.my/product/84227039/3629031588` returned HTTP 200, but the HTML is byte-for-byte the same as the home page: an empty JavaScript shell with no product name, price or schema.org data. |
| Data endpoint | `GET /api/v4/pdp/get_pc?item_id=…&shop_id=…` returned **HTTP 403** with `{"error":90309999,"is_login":false,…}`, Shopee's anti-bot response. |

The only way to get Shopee prices would be running a full browser or forging the signed headers its app sends. Both get around an access control the site has deliberately put in place, which this project rules out. **Verdict: do not build.** The README should explain why.

## Lazada: do not build

| Check | Result |
|---|---|
| robots.txt | Only login and `/undefined/` paths are disallowed for `User-agent: *`. |
| Terms of Service | Checked in a browser on 2026-10-15: Lazada's Terms & Conditions and Community Policy strictly prohibit unauthorised automated access to the platform. |
| Product pages | Two product pages returned HTTP 200 with the full page and no challenge or CAPTCHA. |
| schema.org data | Present (`@type: Product`, name, `availability`), but **the offer has no `price`**, so the generic schema.org plug-in alone cannot track Lazada. |
| Price | Present in the page's embedded data as `"pdt_price":"RM749.00"` (and `RM409.00` on the second page). Not yet checked against the price shown in a browser, or against sale prices. |

A Lazada plug-in would read `name` and `availability` from schema.org and the price from `pdt_price`. That field is an internal tracking value, not a documented format, so it could change without notice. The plug-in must fail with `ParseError` when it is missing, never guess a price.

**Verdict: do not build.** The prices are technically readable, but Lazada's terms forbid automated access. The bot refuses Lazada links with an explanation, so the general schema.org reader never fetches Lazada pages either.

## Summary for the README

> Shopee only serves product data to its own JavaScript app and blocks direct requests (HTTP 403, anti-bot error 90309999). Lazada's pages can be read, but its terms forbid automated access. The tracker supports neither, and refuses their links with an explanation instead of trying.
