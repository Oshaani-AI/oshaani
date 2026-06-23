# Generated manually

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('agents_app', '0021_fix_agentfeedback_utf8mb4'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentPublicShare',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, help_text='Unique token for accessing the shared agent', max_length=64, unique=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether the public share is active')),
                ('expires_at', models.DateTimeField(blank=True, help_text='Expiration date for the share (optional)', null=True)),
                ('access_count', models.IntegerField(default=0, help_text='Number of times the share URL was accessed')),
                ('last_accessed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_shares', to='agents_app.agent')),
                ('shared_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_shared_agents', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='agentpublicshare',
            index=models.Index(fields=['token'], name='agents_app__token_public_idx'),
        ),
        migrations.AddIndex(
            model_name='agentpublicshare',
            index=models.Index(fields=['agent', 'is_active'], name='agents_app__agent_public_idx'),
        ),
    ]

