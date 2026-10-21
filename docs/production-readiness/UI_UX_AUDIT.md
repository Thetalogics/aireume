# UI route audit

The route manifest is `e2e/ui-audit/routes.mjs`. It reads every `path` attribute in `app/frontend/src/App.jsx` and assigns `anonymous`, `redirect`, `platform-admin`, `user`, or `any` for the `*` 404 route. `node --test tests/ui-route-manifest.test.mjs` fails when a route has no role.

Checked roles on 2026-10-21: 65 paths, including login as anonymous, admin as platform-admin, and `*` as any.

Desktop, tablet, and mobile rendering, keyboard walks, and screenshots are not recorded yet. This file is the inventory, not a closed visual audit.
