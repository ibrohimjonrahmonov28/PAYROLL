from django.contrib import admin
from .models import Customer, ProductModel, ProductModelOperation, Article, Operation, ArticleOperation, Order, OrderItem, Box, Ticket, OrderItemSize, CuttingBatch, CuttingBatchItem, BoxQualityInspectionLog


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'phone_number', 'created_at']
    search_fields = ['name', 'code', 'phone_number']


class ProductModelOperationInline(admin.TabularInline):
    model = ProductModelOperation
    extra = 1


@admin.register(ProductModel)
class ProductModelAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'daily_norm', 'created_at']
    search_fields = ['code', 'name']
    inlines = [ProductModelOperationInline]


class ArticleOperationInline(admin.TabularInline):
    model = ArticleOperation
    extra = 1


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'model', 'daily_norm', 'created_at']
    list_filter = ['model']
    search_fields = ['code', 'name']
    inlines = [ArticleOperationInline]


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['code', 'name']


@admin.register(ProductModelOperation)
class ProductModelOperationAdmin(admin.ModelAdmin):
    list_display = ['model', 'operation', 'price_per_unit', 'sequence', 'difficulty']
    list_filter = ['model', 'operation']
    ordering = ['model', 'sequence']


@admin.register(ArticleOperation)
class ArticleOperationAdmin(admin.ModelAdmin):
    list_display = ['article', 'operation', 'price_per_unit', 'sequence', 'difficulty']
    list_filter = ['article', 'operation']
    ordering = ['article', 'sequence']


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class BoxInline(admin.TabularInline):
    model = Box
    extra = 0
    readonly_fields = ['box_number', 'quantity', 'status']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer', 'client_name', 'article', 'total_quantity', 'status', 'created_at']
    list_filter = ['status', 'customer', 'article']
    search_fields = ['order_number', 'client_name', 'customer__name']
    inlines = [OrderItemInline, BoxInline]


@admin.register(Box)
class BoxAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'quantity', 'status', 'created_at']
    list_filter = ['status', 'order']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['stiker_id', 'ticket_code', 'box', 'article_operation', 'quantity', 'price_per_unit', 'total_amount', 'status', 'worker', 'screen_number', 'scanned_at']
    list_filter = ['status', 'screen_number', 'scanned_at']
    search_fields = ['=stiker_code', '=id', 'ticket_code', '=box__box_code', '=box__box_number', 'worker__worker_id', 'worker__first_name', 'worker__last_name']
    readonly_fields = ['stiker_code', 'ticket_code', 'qr_code_image', 'total_amount']
    list_per_page = 50


class OrderItemSizeInline(admin.TabularInline):
    model = OrderItemSize
    extra = 1


@admin.register(OrderItemSize)
class OrderItemSizeAdmin(admin.ModelAdmin):
    list_display = ['order_item', 'size_name', 'planned_quantity', 'total_cut_quantity', 'cut_percentage', 'created_at']
    list_filter = ['order_item__order']
    search_fields = ['size_name', 'order_item__order__order_number', 'order_item__article__code']


class CuttingBatchItemInline(admin.TabularInline):
    model = CuttingBatchItem
    extra = 1


@admin.register(CuttingBatch)
class CuttingBatchAdmin(admin.ModelAdmin):
    list_display = ['name', 'order_item', 'batch_number', 'cutter_name', 'fabric_weight_kg', 'fabric_batch_code', 'total_quantity', 'created_at']
    list_filter = ['order_item__order']
    inlines = [CuttingBatchItemInline]
    search_fields = ['name', 'cutter_name', 'fabric_batch_code', 'order_item__order__order_number']


@admin.register(CuttingBatchItem)
class CuttingBatchItemAdmin(admin.ModelAdmin):
    list_display = [
        'batch', 'order_item_size', 'quantity', 'status', 'real_quantity', 
        'meto_number_start', 'meto_number_end', 'meto_worker_name', 
        'boxes_created_qty', 'remaining_to_box'
    ]
    list_filter = ['status', 'batch__order_item__order']
    search_fields = ['meto_worker_name', 'meto_number_start', 'meto_number_end']


@admin.register(BoxQualityInspectionLog)
class BoxQualityInspectionLogAdmin(admin.ModelAdmin):
    list_display = ['box', 'inspector', 'action_type', 'first_sort_qty', 'second_sort_qty', 'repair_qty', 'defect_qty', 'created_at']
    list_filter = ['action_type', 'created_at']
    search_fields = ['box__box_code', 'box__order__order_number', 'inspector__username']
