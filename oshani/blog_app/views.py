"""Blog views for public pages and API."""
from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.cache import cache_page
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import BlogPost, BlogCategory, BlogTag, BlogComment
from .serializers import (
    BlogPostListSerializer, BlogPostDetailSerializer,
    BlogCategorySerializer, BlogTagSerializer, BlogCommentSerializer
)


class BlogPostViewSet(viewsets.ModelViewSet):
    """ViewSet for blog posts API."""
    queryset = BlogPost.objects.all()
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return BlogPostListSerializer
        return BlogPostDetailSerializer
    
    def get_queryset(self):
        """Filter queryset based on action."""
        queryset = BlogPost.objects.select_related('author', 'category').prefetch_related('tags')
        
        # For public list/detail, only show published posts
        if self.action in ['list', 'retrieve']:
            queryset = queryset.filter(
                status='published',
                published_at__lte=timezone.now()
            )
        # For authenticated users creating/updating, show all their posts
        elif self.action in ['create', 'update', 'partial_update', 'destroy']:
            if self.request.user.is_authenticated:
                queryset = queryset.filter(author=self.request.user)
        
        # Filtering
        category_slug = self.request.query_params.get('category', None)
        tag_slug = self.request.query_params.get('tag', None)
        search = self.request.query_params.get('search', None)
        
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)
        if tag_slug:
            queryset = queryset.filter(tags__slug=tag_slug)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(excerpt__icontains=search) |
                Q(content__icontains=search)
            )
        
        return queryset.order_by('-published_at', '-created_at')
    
    def perform_create(self, serializer):
        """Set author to current user."""
        serializer.save(author=self.request.user)
    
    @action(detail=True, methods=['post'])
    def increment_view(self, request, pk=None):
        """Increment view count for a blog post."""
        post = self.get_object()
        post.increment_view_count()
        return Response({'view_count': post.view_count})


class BlogCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for blog categories (read-only)."""
    queryset = BlogCategory.objects.annotate(
        post_count=Count('posts', filter=Q(posts__status='published'))
    )
    serializer_class = BlogCategorySerializer
    lookup_field = 'slug'


class BlogTagViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for blog tags (read-only)."""
    queryset = BlogTag.objects.annotate(
        post_count=Count('posts', filter=Q(posts__status='published'))
    )
    serializer_class = BlogTagSerializer
    lookup_field = 'slug'


class BlogCommentViewSet(viewsets.ModelViewSet):
    """ViewSet for blog comments."""
    queryset = BlogComment.objects.all()
    serializer_class = BlogCommentSerializer
    permission_classes = [permissions.AllowAny]  # Public comments
    
    def get_queryset(self):
        """Filter comments by post and approval status."""
        queryset = BlogComment.objects.filter(is_approved=True)
        post_id = self.request.query_params.get('post', None)
        if post_id:
            queryset = queryset.filter(post_id=post_id)
        return queryset.order_by('-created_at')
    
    def perform_create(self, serializer):
        """Create comment (requires approval)."""
        serializer.save(is_approved=False)  # Require moderation


# Public page views
def blog_list(request):
    """Public blog listing page."""
    posts = BlogPost.objects.filter(
        status='published',
        published_at__lte=timezone.now()
    ).select_related('author', 'category').prefetch_related('tags').order_by('-published_at')
    
    # Filtering
    category_slug = request.GET.get('category')
    tag_slug = request.GET.get('tag')
    search = request.GET.get('search', '').strip()
    
    # Get category object if filtering by category
    category_obj = None
    if category_slug:
        try:
            category_obj = BlogCategory.objects.get(slug=category_slug)
            posts = posts.filter(category=category_obj)
        except BlogCategory.DoesNotExist:
            category_slug = None
    
    if tag_slug:
        posts = posts.filter(tags__slug=tag_slug)
    if search:
        posts = posts.filter(
            Q(title__icontains=search) |
            Q(excerpt__icontains=search) |
            Q(content__icontains=search)
        )
    
    # Pagination
    paginator = Paginator(posts, 10)  # 10 posts per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get categories and tags for sidebar
    categories = BlogCategory.objects.annotate(
        post_count=Count('posts', filter=Q(posts__status='published'))
    ).order_by('name')
    
    tags = BlogTag.objects.annotate(
        post_count=Count('posts', filter=Q(posts__status='published'))
    ).order_by('name')[:20]  # Top 20 tags
    
    # Recent posts
    recent_posts = BlogPost.objects.filter(
        status='published',
        published_at__lte=timezone.now()
    ).order_by('-published_at')[:5]
    
    context = {
        'page_obj': page_obj,
        'posts': page_obj,
        'categories': categories,
        'tags': tags,
        'recent_posts': recent_posts,
        'current_category': category_slug,
        'current_category_obj': category_obj,  # Pass category object
        'current_tag': tag_slug,
        'search_query': search,
    }
    
    return render(request, 'blog/list.html', context)


def blog_detail(request, slug):
    """Public blog post detail page."""
    post = get_object_or_404(
        BlogPost.objects.select_related('author', 'category').prefetch_related('tags'),
        slug=slug,
        status='published',
        published_at__lte=timezone.now()
    )
    
    # Handle comment submission
    if request.method == 'POST':
        author_name = request.POST.get('author_name', '').strip()
        author_email = request.POST.get('author_email', '').strip()
        author_website = request.POST.get('author_website', '').strip()
        content = request.POST.get('content', '').strip()
        
        if author_name and author_email and content:
            BlogComment.objects.create(
                post=post,
                author_name=author_name,
                author_email=author_email,
                author_website=author_website if author_website else '',
                content=content,
                is_approved=False  # Require moderation
            )
            from django.contrib import messages
            messages.success(request, 'Your comment has been submitted and is pending approval.')
            # Redirect to avoid resubmission on refresh
            from django.shortcuts import redirect
            return redirect('blog:post_detail', slug=slug)
    
    # Increment view count
    post.increment_view_count()
    
    # Get approved comments
    comments = post.comments.filter(is_approved=True, parent__isnull=True).order_by('-created_at')
    
    # Get related posts (same category or tags)
    related_posts = BlogPost.objects.filter(
        status='published',
        published_at__lte=timezone.now()
    ).exclude(id=post.id)
    
    # Try same category first
    if post.category:
        related_posts = related_posts.filter(category=post.category)[:3]
    else:
        # Fallback to same tags
        if post.tags.exists():
            related_posts = related_posts.filter(tags__in=post.tags.all())[:3]
    
    # If still not enough, get recent posts
    if related_posts.count() < 3:
        recent = BlogPost.objects.filter(
            status='published',
            published_at__lte=timezone.now()
        ).exclude(id=post.id).order_by('-published_at')[:3]
        related_posts = list(related_posts) + list(recent)
        related_posts = related_posts[:3]
    
    context = {
        'post': post,
        'comments': comments,
        'related_posts': related_posts,
    }
    
    return render(request, 'blog/detail.html', context)


def blog_category_detail(request, slug):
    """Category detail page."""
    category = get_object_or_404(BlogCategory, slug=slug)
    posts = BlogPost.objects.filter(
        category=category,
        status='published',
        published_at__lte=timezone.now()
    ).order_by('-published_at')
    
    paginator = Paginator(posts, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'category': category,
        'page_obj': page_obj,
        'posts': page_obj,
    }
    
    return render(request, 'blog/category.html', context)


def blog_tag_detail(request, slug):
    """Tag detail page."""
    tag = get_object_or_404(BlogTag, slug=slug)
    posts = BlogPost.objects.filter(
        tags=tag,
        status='published',
        published_at__lte=timezone.now()
    ).order_by('-published_at')
    
    paginator = Paginator(posts, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'tag': tag,
        'page_obj': page_obj,
        'posts': page_obj,
    }
    
    return render(request, 'blog/tag.html', context)
