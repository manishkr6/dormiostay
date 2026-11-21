/**
 * Expo Config Plugin to inject iOS UISceneDelegate support.
 *
 * Required for iOS 27+ SDK runtimes where UIApplication scene manifests
 * are enforced by the system window manager.
 *
 * Ensures React Native is initialized exactly once within SceneDelegate
 * and handles launch/runtime URLs and Universal Links properly.
 */
const { withAppDelegate, withInfoPlist } = require('@expo/config-plugins');

const sceneMethod = [
  '  // UIScene lifecycle support for iOS 27+ SDK',
  '  func application(',
  '    _ application: UIApplication,',
  '    configurationForConnecting connectingSceneSession: UISceneSession,',
  '    options: UIScene.ConnectionOptions',
  '  ) -> UISceneConfiguration {',
  '    let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)',
  '    config.delegateClass = SceneDelegate.self',
  '    return config',
  '  }',
].join('\n');

const sceneClass = [
  '@objc(SceneDelegate)',
  'class SceneDelegate: UIResponder, UIWindowSceneDelegate {',
  '  var window: UIWindow?',
  '',
  '  func scene(',
  '    _ scene: UIScene,',
  '    willConnectTo session: UISceneSession,',
  '    options connectionOptions: UIScene.ConnectionOptions',
  '  ) {',
  '    guard let windowScene = scene as? UIWindowScene else { return }',
  '',
  '    let win = UIWindow(windowScene: windowScene)',
  '    self.window = win',
  '',
  '    if let appDelegate = UIApplication.shared.delegate as? AppDelegate {',
  '      appDelegate.window = win',
  '      if let factory = appDelegate.reactNativeFactory {',
  '        factory.startReactNative(withModuleName: "main", in: win, launchOptions: nil)',
  '      }',
  '      for context in connectionOptions.urlContexts {',
  '        _ = appDelegate.application(UIApplication.shared, open: context.url, options: [:])',
  '      }',
  '      for activity in connectionOptions.userActivities {',
  '        _ = appDelegate.application(UIApplication.shared, continue: activity, restorationHandler: { _ in })',
  '      }',
  '    }',
  '    win.makeKeyAndVisible()',
  '  }',
  '',
  '  func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {',
  '    guard let appDelegate = UIApplication.shared.delegate as? AppDelegate else { return }',
  '    for context in URLContexts {',
  '      _ = appDelegate.application(UIApplication.shared, open: context.url, options: [:])',
  '    }',
  '  }',
  '',
  '  func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {',
  '    guard let appDelegate = UIApplication.shared.delegate as? AppDelegate else { return }',
  '    _ = appDelegate.application(UIApplication.shared, continue: userActivity, restorationHandler: { _ in })',
  '  }',
  '}',
].join('\n');

function injectAppDelegate(contents) {
  // 1. Remove duplicate startReactNative and window allocation from AppDelegate didFinishLaunchingWithOptions
  let result = contents.replace(
    /\n\s*#if os\(iOS\) \|\| os\(tvOS\)[\s\S]*?factory\.startReactNative[\s\S]*?#endif\n/,
    '\n    // In UIScene lifecycle, window allocation and React Native initialization are managed by SceneDelegate.\n',
  );

  // 2. Ensure SceneDelegate exists or is updated with complete URL and activity handlers
  if (result.includes('@objc(SceneDelegate)')) {
    result = result.replace(
      /@objc\(SceneDelegate\)[\s\S]*?class\s+SceneDelegate[\s\S]*?\n\}/,
      sceneClass,
    );
  } else if (result.includes('class ReactNativeDelegate')) {
    result = result.replace(
      /(\n\s*\}\s*)(\nclass\s+ReactNativeDelegate\b)/,
      `\n${result.includes('configurationForConnecting') ? '' : sceneMethod + '\n'}$1\n${sceneClass}\n$2`,
    );
  } else {
    result = result.replace(
      /(class\s+AppDelegate[\s\S]*?)(\n\})/,
      `$1\n${result.includes('configurationForConnecting') ? '' : sceneMethod + '\n'}$2\n\n${sceneClass}\n`,
    );
  }

  // 3. Ensure configurationForConnecting exists inside AppDelegate
  if (!result.includes('configurationForConnecting')) {
    result = result.replace(/(class\s+AppDelegate[\s\S]*?)(\n\})/, `$1\n${sceneMethod}$2`);
  }

  return result;
}

const withIosSceneDelegate = (config) => {
  config = withInfoPlist(config, (config) => {
    config.modResults.UIApplicationSceneManifest = {
      UIApplicationSupportsMultipleScenes: false,
      UISceneConfigurations: {
        UIWindowSceneSessionRoleApplication: [
          {
            UISceneConfigurationName: 'Default Configuration',
            UISceneDelegateClassName: 'SceneDelegate',
          },
        ],
      },
    };
    return config;
  });

  config = withAppDelegate(config, (config) => {
    config.modResults.contents = injectAppDelegate(config.modResults.contents);
    return config;
  });

  return config;
};

withIosSceneDelegate.injectAppDelegate = injectAppDelegate;

module.exports = withIosSceneDelegate;
