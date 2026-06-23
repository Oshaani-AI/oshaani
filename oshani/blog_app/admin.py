"""Admin configuration for blog app."""
from django import forms
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import BlogPost, BlogCategory, BlogTag, BlogComment


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    """Admin for blog categories."""
    list_display = ['name', 'slug', 'post_count', 'created_at']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name', 'description']
    
    def post_count(self, obj):
        """Count of published posts in this category."""
        return obj.posts.filter(status='published').count()
    post_count.short_description = 'Posts'


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    """Admin for blog tags."""
    list_display = ['name', 'slug', 'post_count', 'created_at']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']
    
    def post_count(self, obj):
        """Count of published posts with this tag."""
        return obj.posts.filter(status='published').count()
    post_count.short_description = 'Posts'


class BlogCommentInline(admin.TabularInline):
    """Inline admin for blog comments."""
    model = BlogComment
    extra = 0
    fields = ['author_name', 'author_email', 'content', 'is_approved', 'created_at']
    readonly_fields = ['created_at']
    can_delete = True


class BlogPostAdminForm(forms.ModelForm):
    """Form for blog post to ensure video fields are present."""
    class Meta:
        model = BlogPost
        fields = '__all__'


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    """Admin for blog posts."""
    form = BlogPostAdminForm
    list_display = [
        'title', 'author', 'category', 'status', 'published_at', 
        'view_count', 'reading_time', 'preview_link'
    ]
    list_filter = ['status', 'category', 'tags', 'published_at', 'created_at']
    search_fields = ['title', 'excerpt', 'content', 'meta_title', 'meta_description']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['view_count', 'reading_time_minutes', 'created_at', 'updated_at', 'last_viewed_at', 'notification_sent']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'slug', 'author', 'excerpt', 'content')
        }),
        ('Media', {
            'fields': ('featured_image', 'og_image')
        }),
        ('Video', {
            'fields': ('video', 'video_url'),
            'description': 'Add a video to the post: upload a file (e.g. MP4) or paste a YouTube/Vimeo URL.'
        }),
        ('Categorization', {
            'fields': ('category', 'tags')
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description', 'meta_keywords'),
            'description': 'SEO fields for better search engine visibility'
        }),
        ('Publishing', {
            'fields': ('status', 'published_at')
        }),
        ('Analytics', {
            'fields': ('view_count', 'reading_time_minutes', 'last_viewed_at', 'notification_sent'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    filter_horizontal = ['tags']
    inlines = [BlogCommentInline]
    
    def reading_time(self, obj):
        """Display reading time."""
        return f"{obj.reading_time_minutes} min"
    reading_time.short_description = 'Reading Time'
    
    def preview_link(self, obj):
        """Link to preview the post."""
        if obj.is_published:
            url = obj.get_absolute_url()
            return format_html('<a href="{}" target="_blank">View</a>', url)
        return '-'
    preview_link.short_description = 'Preview'
    
    def save_model(self, request, obj, form, change):
        """Set author if creating new post."""
        if not change:  # Creating new post
            obj.author = request.user
        super().save_model(request, obj, form, change)


@admin.register(BlogComment)
class BlogCommentAdmin(admin.ModelAdmin):
    """Admin for blog comments."""
    list_display = ['author_name', 'post', 'is_approved', 'created_at', 'content_preview']
    list_filter = ['is_approved', 'created_at', 'post']
    search_fields = ['author_name', 'author_email', 'content']
    readonly_fields = ['created_at', 'updated_at']
    actions = ['approve_comments', 'reject_comments']
    
    fieldsets = (
        ('Comment', {
            'fields': ('post', 'content')
        }),
        ('Author', {
            'fields': ('author_name', 'author_email', 'author_website')
        }),
        ('Moderation', {
            'fields': ('is_approved', 'parent')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def content_preview(self, obj):
        """Preview of comment content."""
        return obj.content[:100] + '...' if len(obj.content) > 100 else obj.content
    content_preview.short_description = 'Content'
    
    def approve_comments(self, request, queryset):
        """Approve selected comments."""
        queryset.update(is_approved=True)
        self.message_user(request, f'{queryset.count()} comments approved.')
    approve_comments.short_description = 'Approve selected comments'
    
    def reject_comments(self, request, queryset):
        """Reject selected comments."""
        queryset.update(is_approved=False)
        self.message_user(request, f'{queryset.count()} comments rejected.')
    reject_comments.short_description = 'Reject selected comments'
