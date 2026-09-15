from django.contrib import admin
from .models import Article, Operation, ArticleOperation, Order, Box, Ticket


class ArticleOperationInline(admin.TabularInline):
    model = ArticleOperation
    extra = 1


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'created_at']
    search_fields = ['code', 'name']
    inlines = [ArticleOperationInline]


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['code', 'name']


@admin.register(ArticleOperation)
class ArticleOperationAdmin(admin.ModelAdmin):
    list_display = ['article', 'operation', 'price_per_unit', 'sequence']
    list_filter = ['article', 'operation']
    ordering = ['article', 'sequence']


class BoxInline(admin.TabularInline):
    model = Box
    extra = 0
    readonly_fields = ['box_number', 'quantity', 'status']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'article', 'total_quantity', 'client_name', 'status', 'created_at']
    list_filter = ['status', 'article']
    search_fields = ['order_number', 'client_name']
    inlines = [BoxInline]


@admin.register(Box)
class BoxAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'quantity', 'status', 'created_at']
    list_filter = ['status', 'order']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['ticket_code', 'box', 'article_operation', 'quantity', 'price_per_unit', 'total_amount', 'status', 'worker', 'screen_number', 'scanned_at']
    list_filter = ['status', 'screen_number', 'scanned_at']
    search_fields = ['ticket_code', 'worker__worker_id', 'worker__first_name', 'worker__last_name']
    readonly_fields = ['ticket_code', 'qr_code_image', 'total_amount']
