"""Blog models for SEO-friendly blog posts."""
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.urls import reverse
from django.utils import timezone


class BlogCategory(models.Model):
    """Blog post categories."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('blog:category_detail', kwargs={'slug': self.slug})


class BlogTag(models.Model):
    """Blog post tags."""
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('blog:tag_detail', kwargs={'slug': self.slug})


class BlogPost(models.Model):
    """Blog post model with SEO optimization."""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]
    
    # Basic fields
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True, db_index=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blog_posts')
    
    # Content
    excerpt = models.TextField(max_length=300, help_text="Short summary (max 300 chars) for SEO and previews")
    content = models.TextField(help_text="Full blog post content")
    featured_image = models.ImageField(upload_to='blog/featured/', blank=True, null=True, 
                                      help_text="Featured image for the blog post")
    video = models.FileField(upload_to='blog/videos/', blank=True, null=True,
                             help_text="Optional video file for the post (e.g. MP4)")
    video_url = models.URLField(blank=True, default='',
                                help_text="Optional embed URL (YouTube, Vimeo, etc.). Used if no video file is uploaded.")
    
    # Categorization
    category = models.ForeignKey(BlogCategory, on_delete=models.SET_NULL, null=True, blank=True, 
                                related_name='posts')
    tags = models.ManyToManyField(BlogTag, blank=True, related_name='posts')
    
    # SEO fields
    meta_title = models.CharField(max_length=60, blank=True, 
                                 help_text="SEO title (50-60 chars recommended). If empty, uses title.")
    meta_description = models.TextField(max_length=160, blank=True,
                                       help_text="SEO description (150-160 chars recommended)")
    meta_keywords = models.CharField(max_length=255, blank=True,
                                    help_text="Comma-separated keywords for SEO")
    og_image = models.ImageField(upload_to='blog/og/', blank=True, null=True,
                                help_text="Open Graph image for social sharing (1200x630px recommended)")
    
    # Publishing
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    published_at = models.DateTimeField(null=True, blank=True,
                                       help_text="Publication date (for scheduling)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Analytics
    view_count = models.PositiveIntegerField(default=0)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    
    # Structured data
    reading_time_minutes = models.PositiveIntegerField(default=0, 
                                                      help_text="Estimated reading time in minutes")
    notification_sent = models.BooleanField(default=False, editable=False,
                                            help_text="Whether notification email was sent to users for this post")
    
    class Meta:
        ordering = ['-published_at', '-created_at']
        indexes = [
            models.Index(fields=['status', 'published_at']),
            models.Index(fields=['slug']),
            models.Index(fields=['category', 'status']),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        # Auto-generate slug from title if not provided
        if not self.slug:
            self.slug = slugify(self.title)
            # Ensure uniqueness
            base_slug = self.slug
            counter = 1
            while BlogPost.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        
        # Set published_at when status changes to published
        if self.status == 'published' and not self.published_at:
            self.published_at = timezone.now()
        
        # Calculate reading time (average 200 words per minute)
        if self.content:
            word_count = len(self.content.split())
            self.reading_time_minutes = max(1, round(word_count / 200))
        
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        """Get canonical URL for the blog post."""
        return reverse('blog:post_detail', kwargs={'slug': self.slug})
    
    def get_meta_title(self):
        """Get meta title, fallback to title if not set."""
        return self.meta_title or self.title
    
    def get_meta_description(self):
        """Get meta description, fallback to excerpt if not set."""
        return self.meta_description or self.excerpt[:160]
    
    def get_featured_image_url(self):
        """Get featured image URL or default."""
        if self.featured_image:
            return self.featured_image.url
        return None
    
    def get_og_image_url(self):
        """Get OG image URL or fallback to featured image."""
        if self.og_image:
            return self.og_image.url
        return self.get_featured_image_url()

    def get_video_embed_url(self):
        """Convert YouTube/Vimeo watch URL to embed URL, or return as-is if already embed."""
        if not self.video_url:
            return None
        url = self.video_url.strip()
        # YouTube: watch?v=ID or youtu.be/ID -> embed
        if 'youtube.com/watch' in url:
            try:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(url).query)
                vid = qs.get('v', [None])[0]
                return f'https://www.youtube.com/embed/{vid}' if vid else None
            except Exception:
                return None
        if 'youtu.be/' in url:
            try:
                vid = url.split('youtu.be/')[-1].split('?')[0]
                return f'https://www.youtube.com/embed/{vid}' if vid else None
            except Exception:
                return None
        # Vimeo: vimeo.com/ID -> embed
        if 'vimeo.com/' in url:
            try:
                vid = url.rstrip('/').split('vimeo.com/')[-1].split('?')[0]
                return f'https://player.vimeo.com/video/{vid}' if vid else None
            except Exception:
                return None
        # Already embed or other
        if '/embed/' in url or 'player.vimeo.com' in url:
            return url
        return None
    
    def increment_view_count(self):
        """Increment view count and update last viewed timestamp."""
        self.view_count += 1
        self.last_viewed_at = timezone.now()
        self.save(update_fields=['view_count', 'last_viewed_at'])
    
    @property
    def is_published(self):
        """Check if post is published and visible."""
        if self.status != 'published':
            return False
        if self.published_at and self.published_at > timezone.now():
            return False
        return True


class BlogComment(models.Model):
    """Comments on blog posts."""
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='comments')
    author_name = models.CharField(max_length=100)
    author_email = models.EmailField()
    author_website = models.URLField(blank=True)
    content = models.TextField()
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # For nested comments (reply to another comment)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, 
                              related_name='replies')
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Comment by {self.author_name} on {self.post.title}"
