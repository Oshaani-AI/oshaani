"""Serializers for blog API."""
from rest_framework import serializers
from .models import BlogPost, BlogCategory, BlogTag, BlogComment


class BlogCategorySerializer(serializers.ModelSerializer):
    """Serializer for blog categories."""
    
    class Meta:
        model = BlogCategory
        fields = ['id', 'name', 'slug', 'description', 'created_at']
        read_only_fields = ['id', 'slug', 'created_at']


class BlogTagSerializer(serializers.ModelSerializer):
    """Serializer for blog tags."""
    
    class Meta:
        model = BlogTag
        fields = ['id', 'name', 'slug', 'created_at']
        read_only_fields = ['id', 'slug', 'created_at']


class BlogPostListSerializer(serializers.ModelSerializer):
    """Serializer for blog post list (minimal fields)."""
    author_username = serializers.CharField(source='author.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    tags = BlogTagSerializer(many=True, read_only=True)
    url = serializers.SerializerMethodField()
    
    class Meta:
        model = BlogPost
        fields = [
            'id', 'title', 'slug', 'excerpt', 'author_username', 'category_name',
            'tags', 'featured_image', 'video', 'video_url', 'published_at', 'view_count',
            'reading_time_minutes', 'url'
        ]
        read_only_fields = ['id', 'slug', 'view_count', 'reading_time_minutes']
    
    def get_url(self, obj):
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.get_absolute_url())
        return obj.get_absolute_url()


class BlogPostDetailSerializer(serializers.ModelSerializer):
    """Serializer for blog post detail (all fields)."""
    author_username = serializers.CharField(source='author.username', read_only=True)
    author_email = serializers.EmailField(source='author.email', read_only=True)
    category = BlogCategorySerializer(read_only=True)
    tags = BlogTagSerializer(many=True, read_only=True)
    url = serializers.SerializerMethodField()
    meta_title = serializers.CharField(required=False, allow_blank=True)
    meta_description = serializers.CharField(required=False, allow_blank=True)
    
    class Meta:
        model = BlogPost
        fields = [
            'id', 'title', 'slug', 'excerpt', 'content', 'author', 'author_username',
            'author_email', 'category', 'tags', 'featured_image', 'video', 'video_url',
            'og_image', 'meta_title', 'meta_description', 'meta_keywords', 'status',
            'published_at', 'created_at', 'updated_at', 'view_count',
            'reading_time_minutes', 'url'
        ]
        read_only_fields = [
            'id', 'slug', 'author', 'created_at', 'updated_at', 
            'view_count', 'reading_time_minutes'
        ]
    
    def get_url(self, obj):
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.get_absolute_url())
        return obj.get_absolute_url()
    
    def create(self, validated_data):
        """Create blog post with current user as author."""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['author'] = request.user
        return super().create(validated_data)


class BlogCommentSerializer(serializers.ModelSerializer):
    """Serializer for blog comments."""
    replies = serializers.SerializerMethodField()
    
    class Meta:
        model = BlogComment
        fields = [
            'id', 'post', 'author_name', 'author_email', 'author_website',
            'content', 'is_approved', 'parent', 'replies', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'is_approved', 'created_at', 'updated_at']
    
    def get_replies(self, obj):
        """Get nested replies."""
        if obj.replies.exists():
            return BlogCommentSerializer(obj.replies.filter(is_approved=True), many=True).data
        return []
