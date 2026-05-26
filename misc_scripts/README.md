# misc_scripts

One-off scripts kept for historical reference. Each script here was written
to solve a single specific problem at a single point in time (typically a
data backfill after a schema change), run once, and then left in place as
documentation of what was done.

These scripts are **not** part of the regular application. Typically they
were executed while in the `src/` directory & then moved to the `misc_scripts/`
directory after

If you find yourself rewriting one of these, consider whether the
underlying need is actually a recurring one — in which case it belongs
in `src/` proper or `scripts/`, not here.
