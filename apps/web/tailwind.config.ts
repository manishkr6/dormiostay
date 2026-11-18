import type { Config } from 'tailwindcss';

const color = (name: string) => `var(--color-${name})`;
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    screens: { sm: '640px', lg: '1024px', xl: '1440px' },
    extend: {
      colors: {
        primary: {
          100: color('primary-100'),
          600: color('primary-600'),
          700: color('primary-700'),
          800: color('primary-800'),
          900: color('primary-900'),
        },
        ink: color('text'),
        muted: color('text-muted'),
        background: color('bg'),
        surface: color('surface'),
        'surface-muted': color('surface-muted'),
        border: color('border'),
        'border-strong': color('border-strong'),
        accent: color('accent'),
        highlight: color('highlight'),
        success: color('success'),
        'success-bg': color('success-bg'),
        error: color('error'),
        'error-bg': color('error-bg'),
        info: color('info'),
        'info-bg': color('info-bg'),
        'warning-text': color('warning-text'),
        'warning-bg': color('warning-bg'),
      },
      fontFamily: { heading: ['var(--font-heading)'], sans: ['var(--font-body)'] },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        modal: 'var(--radius-modal)',
        full: 'var(--radius-full)',
      },
      boxShadow: { card: 'var(--shadow-card)', pop: 'var(--shadow-pop)' },
      spacing: Object.fromEntries(
        [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20].map((n) => [n, `var(--space-${n})`]),
      ),
    },
  },
} satisfies Config;
