# Shared navigation and list design

Local date: 2026-10-07 (Africa/Tripoli).

## Owner scope

The owner requested a comprehensive modern redesign of menus across the
platform, allowing changes to the previous visual treatment while retaining
the centre's identity and logo colours. The earlier source-only instruction
remains: do not launch the platform or operate a browser. No database, schema,
deployment, installation or Git mutation is included.

## Implemented design

- All four workspace roles use a consistent white navigation rail with the
  existing Youth Centre logo, a role badge, coherent line icons, labelled
  groups and a strong current-page state. Main selection uses logo blue
  `#144BAA`; navy `#092969` anchors headings; red `#F51A17` adds a limited
  selection accent. Values derive from the original shared brand tokens.
- Student navigation has Overview, Learning, My record and Communication
  groups. Teacher navigation has Overview, Teaching, Class records and
  Communication groups. Administrator retains its complete academic, people,
  operations and finance inventory. Researcher navigation separates workspace,
  data/review and governance without linking into other roles.
- One shared primary menu per portal role replaces layout reliance on
  page-specific sidebar overrides. All established primary destinations and
  Student research identifiers remain. Legacy child `portal_nav` definitions
  are no longer consumed by the shared layout; their content blocks still are.
- Help and Display remain visible at the bottom of the rail. Account settings
  and POST sign-out remain in the top-right native account disclosure, now
  using an avatar pill, descriptions, consistent icons and a bounded white
  card. Researcher sign-out retains its own endpoint.
- Context tabs, notification filters, conversation/contact tabs, message action
  menus, emoji filters, native single-choice selects, record/report section
  menus and pagination share spacing, borders, focus and current states.
- Course outlines, lesson lists, review/course cards and table records have
  clear separators, lighter surfaces and restrained hover states. Content,
  learning statuses, financial values and write workflows are unchanged.
- Desktop rail geometry is consistent across roles. Existing mobile drawer
  handling, focus restoration and no-JavaScript navigation remain. Wide
  context tabs scroll horizontally on small screens. Native selects remain
  native; multi-selects are not converted into single-choice widgets. Reduced
  motion, forced-colour select fallback and print behavior remain supported
  in source.

The previous single-title rule, full-page messaging surface and role-scoped
sidebar pixel offset storage remain. No navigation URL, identity, message or
learning record is added to browser storage by this redesign.

## Source locations

The shared component macros are in `app/templates/_figma_ui.html`; the new
presentation layer is `app/static/css/navigation.css`, loaded by all three
shared layouts (the portal layout covers both Student and Teacher). Role
inventories live in the layouts and Student/Teacher navigation partials.

`app/blueprints/workspace.py` adds only a guarded static Administrator navigation
helper for shared Help/Display pages. It performs no query and exposes no
non-Administrator menu. Account settings already receives this inventory.
Native section navigation markers were added to the Student record and
Administrator financial report templates; lesson/question/discussion templates
receive presentation classes only. No route, form field name, authorization
rule, CSRF contract, collector semantics or business transaction changed.

## Verification actually performed

- All 292 application Python sources parse as ASTs; all 190 Jinja sources parse.
- Offline rendering with StrictUndefined covered 65 role/destination/shared-page/
  section states: 47 primary destinations, 12 Help/Display/Account combinations,
  three record sections and three financial report sections. Verified complete
  role URL inventories (Student 12, Teacher 10, Administrator 18, Researcher 7),
  selected primary destination, one header title, unique IDs, account settings,
  native POST logout with CSRF and decorative SVG semantics.
- The first synthetic direct-render fixture omitted the new context helper.
  Supplying the actual guarded helper corrected the fixture; the subsequent
  full 65-state source review passed. No application view was dispatched.
- Network connection and SQL connection/execution guards were installed before
  blueprint imports for the offline review. No application factory, database,
  server or browser was used. No automated suite or new test package was run
  or created.
- The new CSS was manually reviewed against existing selector specificity,
  breakpoint, disclosure and messaging geometry rules. Its lexical delimiters
  and changed-file whitespace were checked. No CSS parser was available in
  the existing environments; none was installed.

Browser visual acceptance, actual responsive geometry, keyboard interaction
and comprehensive workflow regression remain unverified under the owner's
source-only instruction. No fresh visual/runtime acceptance is claimed.
