const path = require('path');

module.exports = {
  preset: 'jest-expo',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  moduleNameMapper: {
    '^react$': path.resolve(__dirname, '../../node_modules/react'),
    '^react-dom$': path.resolve(__dirname, '../../node_modules/react-dom'),
  },
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo.*|@expo.*|react-navigation|@react-navigation.*|@sentry/react-native|nativewind|react-native-css-interop|lucide-react-native)/)',
    '/node_modules/react-native-reanimated/plugin/',
    '/node_modules/@react-native/babel-preset/',
  ],
  testPathIgnorePatterns: ['/node_modules/'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json'],
};
