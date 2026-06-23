# Generated manually for slug URL support

from django.db import migrations, models
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    """Populate slug field for existing agents."""
    Agent = apps.get_model('agents_app', 'Agent')
    
    for agent in Agent.objects.all():
        base_slug = slugify(agent.name) or 'agent'
        slug = base_slug
        counter = 1
        
        # Ensure uniqueness
        while Agent.objects.filter(slug=slug).exclude(pk=agent.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        agent.slug = slug
        agent.save(update_fields=['slug'])


def reverse_slugs(apps, schema_editor):
    """Reverse migration - set slugs to empty."""
    Agent = apps.get_model('agents_app', 'Agent')
    Agent.objects.all().update(slug='')


class Migration(migrations.Migration):

    dependencies = [
        ('agents_app', '0027_add_profile_picture_and_mobile_number'),
    ]

    operations = [
        # Step 1: Add slug field (nullable initially)
        migrations.AddField(
            model_name='agent',
            name='slug',
            field=models.SlugField(
                max_length=220,
                blank=True,
                null=True,
                help_text='URL-friendly identifier (auto-generated from name)'
            ),
        ),
        # Step 2: Populate slugs for existing agents
        migrations.RunPython(populate_slugs, reverse_slugs),
        # Step 3: Make slug unique and non-nullable
        migrations.AlterField(
            model_name='agent',
            name='slug',
            field=models.SlugField(
                max_length=220,
                unique=True,
                blank=True,
                db_index=True,
                help_text='URL-friendly identifier (auto-generated from name)'
            ),
        ),
    ]
