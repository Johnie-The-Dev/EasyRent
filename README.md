This is a house renting website called Easy Rent.
The mission is to enable tenants especially campus students and fast moving workers to rent a house from anywhere and allow landlords to manage their property from anywhere.
It is my happiness to make this mission a success.
If you are willing you can join me in building this great project.

## Resetting database data

To clear Django-managed data from the configured PostgreSQL database while keeping
its tables and migration history, run:

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
