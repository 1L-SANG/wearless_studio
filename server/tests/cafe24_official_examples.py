"""Visible fields from cafe24-docs/OFFICIAL_EXAMPLES_20261101.md, with ellipses omitted."""
from copy import deepcopy


_EXAMPLES = {
    'product_setting': {'product': {
        'shop_no': 1, 'display_price_scope': 'A', 'calculate_price_based_on': 'P',
        'price_rounding_unit': '1', 'price_rounding_rule': 'U',
    }},
    'image_setting': {'image': {'shop_no': 1, 'product_image_size': {
        'detail_image_width': 500, 'detail_image_height': 500,
        'list_image_width': 300, 'list_image_height': 300,
        'tiny_image_width': 220, 'tiny_image_height': 220,
        'zoom_image_width': 500, 'zoom_image_height': 500,
        'small_image_width': 100, 'small_image_height': 100,
    }}},
    'variants': {'variants': [{
        'shop_no': 1, 'variant_code': 'P000000R000C',
        'options': [{'name': 'Color', 'value': 'Blue'}, {'name': 'Size', 'value': 'Small'}],
        'custom_variant_code': '', 'display': 'T', 'selling': 'T', 'display_order': 1,
        'additional_amount': '0.00', 'use_inventory': 'T', 'important_inventory': 'A',
        'inventory_control_type': 'A', 'display_soldout': 'T', 'quantity': 6,
        'safety_inventory': 7, 'image': 'https://...',
    }]},
    'update_variants_request': {'shop_no': 1, 'requests': [{
        'variant_code': 'P000000P000B', 'custom_variant_code': 'OPTION_CUSTOM_CODE',
        'display': 'T', 'selling': 'F', 'display_order': 1, 'additional_amount': '-1000.00',
        'quantity': 15, 'use_inventory': 'T', 'important_inventory': 'A',
        'inventory_control_type': 'A', 'display_soldout': 'T', 'safety_inventory': 6,
    }]},
    'update_variants': {'variant': [{
        'shop_no': 1, 'variant_code': 'P000000P000B', 'custom_variant_code': '...',
        'options': [{'name': 'Color', 'value': 'Black'}, {'name': 'Size', 'value': 'L'}],
        'display': 'T', 'selling': 'F', 'display_order': 1, 'additional_amount': '-1000.00',
        'inventories': {
            'shop_no': 1, 'variant_code': 'P000000P000B', 'quantity': 15,
            'use_inventory': 'T', 'important_inventory': 'A', 'inventory_control_type': 'B',
            'display_soldout': 'T', 'safety_inventory': 6,
        },
    }]},
    'inventory_request': {'shop_no': 1, 'request': {
        'use_inventory': 'T', 'important_inventory': 'A', 'inventory_control_type': 'A',
        'display_soldout': 'T', 'quantity': 3, 'safety_inventory': 8, 'origin_code': 'W00000BT',
    }},
    'inventory': {'inventory': {
        'shop_no': 1, 'variant_code': 'P00000GR000A', 'use_inventory': 'T', 'quantity': 3,
    }},
    'additional_images_request': {'shop_no': 1, 'request': {
        'additional_image': ['data:image/png;base64,...'],
    }},
    'additional_images': {'additionalimage': {'shop_no': 1, 'additional_image': [{
        'big': 'http://{domain}/web/product/extra/big/....jpeg', 'medium': '...', 'small': '...',
    }]}},
    'product_state': {'product': {'shop_no': 1, 'product_no': 20, 'display': 'T', 'selling': 'F'}},
    'set_product_state_request': {'shop_no': 1, 'request': {'display': 'T', 'selling': 'T'}},
    'set_product_state': {'product': {'shop_no': 1, 'product_no': 20, 'display': 'T', 'selling': 'T'}},
}


def official_example(name):
    return deepcopy(_EXAMPLES[name])
