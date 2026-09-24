# ForgeAgent frontend contract

Retain only the Twitter-inspired frontend. `/` opens the workspace and `/twitter` remains compatible. Do not recreate the removed variants or gallery.

The shell and visual system are in `src/variants/twitter/`. Primary pages are in `src/TaskViews.tsx`, the task workspace is in `src/TaskWorkspace.tsx`, and creation/search dialogs are in `src/Actions.tsx`. Ancillary pages remain in `src/Views.tsx`. State migration and browser-only execution simulation are in `src/runtime.ts` and `src/useRuntime.ts`.

Preserve `forge-ui-v1-twitter` and `forge-twitter-bookmarks` storage keys. All execution is a local frontend demo. No backend or real model/tool calls.

The user-approved 24 recommendations are implemented. See `docs/ForgeAgent-24项界面改版验收.md` for exact scope and validation. The original 30-point review remains as historical rationale; do not assume its additional recommendations are all implemented. Preserve state/phase/wait-reason separation, version-bound evidence, pending approvals and recovery across navigation, URL filters, and event reading position.
