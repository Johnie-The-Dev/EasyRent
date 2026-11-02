# EasyRent Security Review

## Scope and method

This was a static review of authentication, password recovery, tenant/landlord
authorization, bookings, and admin configuration, supplemented by
`python manage.py check --deploy` in the configured web container. It was not
an external penetration test or a review of production infrastructure.

## Findings

| # | Severity | File | Lines | Finding | Confidence |
|---|----------|------|-------|---------|------------|
| 1 | HIGH | [docker-compose.yml](./docker-compose.yml), [settings.py](./EasyRent/settings.py) | `docker-compose.yml:28-34`; `settings.py:17,196-197` | The Compose configuration publishes the Django development server with `DEBUG=True` and a hard-coded weak `SECRET_KEY`. With DEBUG enabled, secure session and CSRF cookies are disabled. This configuration is unsafe if exposed beyond local development. Django's deployment check also reports missing HTTPS redirect and HSTS settings; configure these in Django or enforce them at a trusted TLS-terminating proxy. | 10/10 |
| 2 | HIGH | [views.py](./listings/views.py) | `1419-1420,1513-1546` | Password recovery uses a six-digit code valid for ten minutes, with no attempt limit or request throttling. An attacker able to reach the recovery flow can make repeated guesses and can repeatedly invalidate outstanding codes. Add per-account and per-source throttling, attempt limits, and monitoring. | 9/10 |
| 3 | MEDIUM | [views.py](./listings/views.py) | `1550-1572` | Password-reset completion checks only for an eight-character minimum rather than calling Django's configured password validators. Common or otherwise weak passwords can therefore bypass the stronger password policy used during signup. Call `validate_password` and return its validation errors to the user. | 10/10 |
| 4 | LOW | [views.py](./listings/views.py) | `319-321` | Logout is a state-changing GET endpoint. A third-party page can cause a signed-in user's browser to sign out. Require POST and retain CSRF protection. | 10/10 |

No critical findings were identified in the reviewed scope. The role and
ownership checks inspected for tenant/landlord actions and operations-admin
views provide useful authorization boundaries; they do not replace
deployment hardening or runtime testing.

## Recommended order

1. Do not expose the current Compose configuration to the internet. Use a
   production WSGI/ASGI server, a private random secret, `DEBUG=False`, HTTPS,
   secure cookies, and deliberate host/proxy/HSTS settings.
2. Throttle password-reset requests and verification attempts, and limit
   failed guesses for each active reset flow.
3. Apply Django's configured password validators when setting a reset password.
4. Change logout to POST.
