/**
 * Format a number to show up to 3 decimal places, removing trailing zeros
 * Examples:
 *   627 → "627"
 *   627.0 → "627.0" (if it has a decimal component)
 *   627.123 → "627.123"
 *   627.12 → "627.12"
 *   627.1 → "627.1"
 *   0 → "0"
 */
export function formatAxisValue(value) {
  if (typeof value !== 'number' || isNaN(value)) {
    return String(value);
  }
  
  // Round to 3 decimal places to avoid floating point precision issues
  const rounded = Math.round(value * 1000) / 1000;
  
  // Check if the number is an integer (no decimal part)
  const isInteger = Math.abs(rounded - Math.round(rounded)) < 0.0001;
  
  if (isInteger) {
    // For integers, show without decimals
    return String(Math.round(rounded));
  }
  
  // For non-integers, convert to string with up to 3 decimal places
  const str = rounded.toFixed(3);
  
  // Remove trailing zeros, but keep the decimal point if there are any non-zero decimals
  // This ensures:
  // - 627.100 → "627.1"
  // - 627.120 → "627.12"
  // - 627.123 → "627.123"
  const trimmed = str.replace(/\.?0+$/, '');
  
  // If we removed everything after the decimal point, check if we should keep .0
  // But since we already determined it's not an integer, we should have decimals
  return trimmed;
}
