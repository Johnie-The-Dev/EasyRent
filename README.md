This is a house renting website called Easy Rent.
The mission is to enable tenants especially campus students and fast moving workers to rent a house from anywhere and allow landlords to manage their property from anywhere.
It is my happiness to make this mission a success.
If you are willing you can join me in building this great project.

## Resetting database data

Superusers can open the manager dashboard at `/admin/ops/` to review landlords
and tenants, search accounts, inspect linked activity, suspend/reactivate an
account, or delete an account and its linked EasyRent records. Deletion requires
typing the account username. Other application records are available in Django
admin at `/admin/`. Landlord tenancy forms can optionally link a tenancy to an
existing tenant account; booking-based move-ins are linked automatically, so
account review and deletion can include that tenancy history. Older manual
tenancy records remain unlinked until someone matches them to an account.
Suspending a landlord hides their listings and contact details from tenants
without deleting property, booking, or tenancy records.

The site and Django admin use separate browser sessions, so signing in or out
of one does not replace the other. Admin sessions and CSRF tokens are scoped
to `/admin/`, so signing in to the site does not invalidate an open admin
form. After this separation is enabled, administrators may need to sign in to
the admin again once.

The manager dashboard also offers a full data reset. It requires typing both
the configured database name and `DELETE ALL DATA`; it removes all Django-managed
records, including manager accounts, so create a new superuser afterward.
Tables and migration history are preserved.

Alternatively, to clear Django-managed data from the configured PostgreSQL
database while keeping its tables and migration history, run:

```sh
docker compose exec web python manage.py reset_data --confirm-database easy_rent
```

This clears all Django-managed records in the selected database, not only
user-owned records. That includes users, properties, bookings, tenancies, payment
history, and sessions, so everyone will need to sign in again. Django may
recreate its built-in content types and permissions.
The command refuses to run unless `--confirm-database` exactly matches the
configured database name. For a non-default database alias, provide `--database`
as well. This operation is destructive; make a backup first if the data may be
needed later.
