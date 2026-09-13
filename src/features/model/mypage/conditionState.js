import { BRAND_USE_CATEGORIES } from '../../../lib/brandUseCategories.js';

export function toggleAllowedCategory(allowed, category) {
  const values = BRAND_USE_CATEGORIES.filter(value => allowed.includes(value));
  if (!BRAND_USE_CATEGORIES.includes(category)) return values;
  if (values.includes(category)) return values.length > 1 ? values.filter(value => value !== category) : values;
  return BRAND_USE_CATEGORIES.filter(value => value === category || values.includes(value));
}
