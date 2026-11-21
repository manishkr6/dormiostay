const React = require('react');

const MockIcon = (props) => React.createElement('MockIcon', props);

module.exports = new Proxy(
  {},
  {
    get: () => MockIcon,
  },
);
