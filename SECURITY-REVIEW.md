# EasyRent Security Review

## Scope and method

This was a static review of authentication, password recovery, tenant/landlord
authorization, bookings, and admin configuration, supplemented by
`python manage.py check --deploy` in the configured web container. It was not
an external penetration test or a review of production infrastructure.

## Findings

| # | Severity | File | Status | Finding | Confidence |
|---|----------|------|--------|---------|------------|
| 1 | HIGH | [docker-compose.yml](./docker-compose.yml), [settings.py](./EasyRent/settings.py) | Open | The Compose configuration publishes the Django development server with `DEBUG=True` and a hard-coded weak `SECRET_KEY`. With DEBUG enabled, secure session and CSRF cookies are disabled. This configuration is unsafe if exposed beyond local development. Django's deployment check also reports missing HTTPS redirect and HSTS settings; configure these in Django or enforce them at a trusted TLS-terminating proxy. | 10/10 |
| 2 | HIGH | [views.py](./listings/views.py) | Fixed | Password recovery previously used a six-digit code valid for ten minutes without attempt limits or request throttling. Requests are now limited per email and active codes expire after five failed verification attempts. | 9/10 |
| 3 | MEDIUM | [views.py](./listings/views.py) | Fixed | Password-reset completion previously checked only a fixed length. It now applies Django's configured password validators and displays their validation messages. | 10/10 |
| 4 | LOW | [views.py](./listings/views.py), [base.html](./listings/templates/base.html) | Fixed | Logout previously changed state on GET. It now requires POST and is submitted through a CSRF-protected form. | 10/10 |

No critical findings were identified in the reviewed scope. The role and
ownership checks inspected for tenant/landlord actions and operations-admin
views provide useful authorization boundaries; they do not replace
deployment hardening or runtime testing.

## Recommended order

1. Do not expose the current Compose configuration to the internet. Use a
   production WSGI/ASGI server, a private random secret, `DEBUG=False`, HTTPS,
   secure cookies, and deliberate host/proxy/HSTS settings.
2. Consider adding IP-based password-reset rate limits and monitoring in
   addition to the current per-email limits.
