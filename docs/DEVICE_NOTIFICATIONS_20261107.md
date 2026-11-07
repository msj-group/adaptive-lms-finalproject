# Device notifications and installed-device sign-in

The owner requested system notifications while Youth LMS is closed and persistent
sign-in on installed devices. This uses standard Web Push, an explicit permission
button in Account settings, and Flask-Login's signed remember cookie. It does not
cache personal pages, credentials, books, media or notifications offline.

## Delivery and account boundaries

`device_push_subscriptions` stores a recipient, auth version, endpoint/key pair,
expiry, and a delivery cursor. It is additive, independent of academic content,
and never changes enrollment, progress, assessments or research records. JSON
registration and opt-out require an active login and CSRF; endpoint providers,
key sizes and P-256 points are validated. The sender accepts no redirects and
has a five-second network timeout. Endpoint/key values must never enter logs.

One worker polls stored notifications every five seconds and coalesces each
device's pending updates into a generic lock-screen message. No private message
body or course/student name is sent. Notification clicks open the ordinary
authenticated inbox, which rechecks recipient/target access. It leaves another
window's unsaved quiz/form intact. Existing post-domain notification production
retains its best-effort contract: this cannot recover a notification that was
never stored. Stored deliveries retry with backoff and Retry-After support;
404/410 endpoints are disabled. A crash after remote acceptance and before the
cursor commit can resend a generic update; the stable notification tag coalesces
it. This is not exactly-once delivery or a guarantee of OS delivery timing.

The worker also synchronizes genuinely due curriculum releases for opted-in
active Students, using the existing unique recipient/release constraint and
native publication checks. It does not invent a visit, login, completion,
research event or notification for testing. The inbox still synchronizes releases
for users without device subscriptions on their ordinary visit.

Logout disables this browser's signed device binding. Account suspension or an
auth-version change invalidates both remembered access and worker eligibility.
Subscriptions are not silently transferred between accounts. They expire after
at most 90 days and a new opt-in starts after the recipient's current notification
cursor, preventing a backlog of old pushes. Devices can opt out in Account or
browser/OS settings. Android/Chrome battery and notification settings govern
actual background delivery; permission is never requested on page load.

## Remembered access

The sign-in checkbox is selected by default when opened as an installed app;
its local opt-out stores only a preference. An already authenticated installed
app can enable the same signed remember cookie through a CSRF-protected POST.
The cookie lasts a fixed 30 days, uses HttpOnly/SameSite=Lax and HTTPS Secure in
production, and contains the existing revocable user/auth-version identity.
No password/token is stored in JavaScript, localStorage or the service-worker
cache. Logout clears it. Session restoration does not create a fake login or
change the Researcher password-only authentication policy.

## Activation

Apply exactly `a4b9d0c21e76 -> c5d7e8f901ab` on MySQL/InnoDB after a protected
backup. No content import is included. Keep VAPID private PEM outside Git,
readable only to root/the application group. Keep its directory root-owned with
mode 0750/group adaptiveapp and the PEM mode 0640. The existing /etc application
configuration directory remains root-only; the worker cannot traverse it. Configure:

```
DEVICE_PUSH_ENABLED=true
PUSH_VAPID_PRIVATE_KEY_FILE=/srv/adaptiveenglishlms/shared/webpush-keys/vapid-private.pem
PUSH_VAPID_PUBLIC_KEY=<public uncompressed P-256 key, URL-safe base64>
PUSH_VAPID_SUBJECT=https://102.203.203.125
```

Install the reviewed `adaptiveenglishlms-push.service`. Inspect configuration
with `flask --app wsgi:application device-push-worker --inspect`; it sends/writes nothing.
Start the worker only after source/schema/key validation. A rollback stops the
new worker and restores the previous application/helper/environment files;
retain the additive subscription table. Never downgrade a populated table.
Keep VPS restart/service approval separate from preparation. Existing service
workers update after open windows finish; do not force reload an active lesson
or assessment. Account settings explain when closing/reopening is required.

## Verification boundary

Use source/Jinja/JavaScript/mapper and offline MySQL DDL checks, then scoped real
MySQL readback. Do not create an automated test suite, fabricated subscription,
user login, learner progress or a notification sent to real users as a check.
Actual permission, closed-app delivery and restored sign-in require an owner
device and are not established by static review or server health alone.

Primary references: [Push API](https://www.w3.org/TR/push-api/),
[Microsoft PWA push](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/push),
[pywebpush](https://github.com/web-push-libs/pywebpush),
[Flask-Login](https://flask-login.readthedocs.io/en/latest/).

Executed locally: Python/JavaScript syntax, 319 Jinja templates, ORM mappers,
offline MySQL DDL and dependency consistency passed. A protected full backup
preceded the exact local migration. The 95 preexisting non-version tables retained
identical fingerprints; the new InnoDB table is empty. A separate private
development VAPID key pair passed the real read-only worker inspection with zero
subscriptions. No actual notification, fake account/session or browser acceptance
was used. Production activation and physical device verification remain pending.
