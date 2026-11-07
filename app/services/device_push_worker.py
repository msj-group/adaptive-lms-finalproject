"""One small worker; reads actual notifications and never records learner activity."""
import time
import click
from sqlalchemy import or_, select, text

from app.extensions import db
from app.models import User
from app.models.device_push import DevicePushSubscription as Device
from app.services.device_push import deliver_device, now, settings


def register_device_push_cli(app):
    @app.cli.command("device-push-worker")
    @click.option("--inspect", is_flag=True, help="Read configuration/counts only; send and write nothing.")
    def worker(inspect):
        enabled, _, _, _ = settings()
        if not enabled:
            raise click.ClickException("Device notifications are disabled in this environment.")
        if inspect:
            db.session.execute(text("SET TRANSACTION READ ONLY"))
            count = db.session.query(Device.id).filter_by(enabled=True).count()
            db.session.rollback()
            click.echo(f"Configuration ready; {count} enabled device subscriptions. No messages sent.")
            return
        # One worker across releases. The advisory lock holds no academic rows.
        with db.engine.connect() as lease:
            if lease.scalar(text("SELECT GET_LOCK('aelms-device-push-v1', 0)")) != 1:
                raise click.ClickException("A device notification worker is already running.")
            after = 0
            try:
                while True:
                    # Connection loss also loses the advisory lease: exit so
                    # systemd restarts rather than running without ownership.
                    if lease.scalar(text("SELECT IS_USED_LOCK('aelms-device-push-v1') = CONNECTION_ID()")) != 1:
                        raise click.ClickException("Device worker lease was lost.")
                    try:
                        ids = list(db.session.scalars(select(Device.id).where(Device.enabled.is_(True),
                            Device.id > after, or_(Device.retry_at.is_(None), Device.retry_at <= now()))
                            .order_by(Device.id).limit(200)))
                        users = list(db.session.scalars(select(User).join(Device, Device.user_id == User.id)
                            .where(Device.id.in_(ids), User.role == "student", User.status == "active",
                                   Device.auth_version == User.auth_version).distinct())) if ids else []
                        from app.services.curriculum_notifications import sync_curriculum_notices
                        for user in users:
                            sync_curriculum_notices(user, background=True)
                        db.session.rollback()
                        for device_id in ids:
                            deliver_device(device_id)
                        after = ids[-1] if len(ids) == 200 else 0
                    except click.ClickException:
                        raise
                    except Exception as error:
                        db.session.rollback()
                        app.logger.warning("Device worker cycle deferred type=%s", type(error).__name__)
                    finally:
                        db.session.remove()
                    time.sleep(5)
            finally:
                lease.execute(text("SELECT RELEASE_LOCK('aelms-device-push-v1')"))
