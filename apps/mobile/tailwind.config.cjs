const tokens = require('./src/theme/tokens.json');
module.exports = {
  content: ['./app/**/*.{ts,tsx}', './src/**/*.{ts,tsx}'],
  presets: [require('nativewind/preset')],
  theme: {
    extend: {
      colors: tokens.colors,
      fontFamily: { body: [tokens.fonts.body], heading: [tokens.fonts.heading] },
    },
  },
  plugins: [],
};
