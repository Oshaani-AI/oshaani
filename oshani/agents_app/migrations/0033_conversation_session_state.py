from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents_app', '0032_agent_api_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='session_state',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Structured session data (e.g. exam progress: question number, answers)',
            ),
        ),
    ]
