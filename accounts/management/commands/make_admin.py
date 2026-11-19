from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Make an existing account (e.g. one that signed in with Google) a superuser/admin."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--remove", action="store_true", help="Take admin rights away instead.")

    def handle(self, email, remove=False, **opts):
        user = User.objects.filter(email__iexact=email.strip()).first()
        if user is None:
            raise CommandError(f"No account with email {email}. They need to sign in once first.")
        user.is_superuser = user.is_staff = not remove
        user.save(update_fields=["is_superuser", "is_staff"])
        self.stdout.write(self.style.SUCCESS(f"{user.email} is {'no longer' if remove else 'now'} an admin."))
