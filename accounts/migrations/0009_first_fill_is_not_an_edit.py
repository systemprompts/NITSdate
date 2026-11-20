from django.db import migrations

# Answers the sign-up form doesn't ask for start empty, so the first edit ever counted on one of
# them was filling it in. That no longer counts as an edit: take it back out of the counters.
OPTIONAL = ["photo", "year", "branch", "looking_for", "personality", "ideal_type", "bio", "interests",
            "skills", "hometown", "languages"]


def uncount_first_fill(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.all().only("id", "field_meta"):
        meta, changed = profile.field_meta or {}, False
        for key in OPTIONAL:
            m = meta.get(key)
            if m and m.get("edits", 0) >= 1:
                m["edits"] -= 1
                if m["edits"] == 0:
                    m["last_edited"] = None
                changed = True
        if changed:
            Profile.objects.filter(pk=profile.pk).update(field_meta=meta)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_notification_settings"),
    ]

    operations = [
        migrations.RunPython(uncount_first_fill, migrations.RunPython.noop),
    ]
