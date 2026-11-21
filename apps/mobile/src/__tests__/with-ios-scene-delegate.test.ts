// eslint-disable-next-line @typescript-eslint/no-require-imports
const withIosSceneDelegate = require('../../plugins/with-ios-scene-delegate.cjs');

describe('withIosSceneDelegate Expo Config Plugin', () => {
  const pristineAppDelegate = `internal import Expo
import React
import ReactAppDependencyProvider

@main
class AppDelegate: ExpoAppDelegate {
  var window: UIWindow?

  var reactNativeDelegate: ExpoReactNativeFactoryDelegate?
  var reactNativeFactory: RCTReactNativeFactory?

  public override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    let delegate = ReactNativeDelegate()
    let factory = ExpoReactNativeFactory(delegate: delegate)
    delegate.dependencyProvider = RCTAppDependencyProvider()

    reactNativeDelegate = delegate
    reactNativeFactory = factory

#if os(iOS) || os(tvOS)
    window = UIWindow(frame: UIScreen.main.bounds)
    factory.startReactNative(
      withModuleName: "main",
      in: window,
      launchOptions: launchOptions)
#endif

    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // Linking API
  public override func application(
    _ app: UIApplication,
    open url: URL,
    options: [UIApplication.OpenURLOptionsKey: Any] = [:]
  ) -> Bool {
    return super.application(app, open: url, options: options) || RCTLinkingManager.application(app, open: url, options: options)
  }

  // Universal Links
  public override func application(
    _ application: UIApplication,
    continue userActivity: NSUserActivity,
    restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void
  ) -> Bool {
    let result = RCTLinkingManager.application(application, continue: userActivity, restorationHandler: restorationHandler)
    return super.application(application, continue: userActivity, restorationHandler: restorationHandler) || result
  }
}

class ReactNativeDelegate: ExpoReactNativeFactoryDelegate {
}
`;

  it('exports a valid config plugin function and injectAppDelegate helper', () => {
    expect(typeof withIosSceneDelegate).toBe('function');
    expect(typeof withIosSceneDelegate.injectAppDelegate).toBe('function');
  });

  it('transforms pristine AppDelegate so that startReactNative appears exactly once', () => {
    const result = withIosSceneDelegate.injectAppDelegate(pristineAppDelegate);

    // Exactly one startReactNative call (inside SceneDelegate)
    const matches = result.match(/factory\.startReactNative/g) || [];
    expect(matches.length).toBe(1);

    // Exactly one SceneDelegate class
    const sceneMatches = result.match(/class\s+SceneDelegate/g) || [];
    expect(sceneMatches.length).toBe(1);

    // Injects UIScene configuration method in AppDelegate
    expect(result).toContain('configurationForConnecting');

    // Handles connectionOptions.userActivities for Universal Links at launch
    expect(result).toContain('connectionOptions.userActivities');

    // Window creation removed from AppDelegate.didFinishLaunchingWithOptions
    expect(result).toContain(
      '// In UIScene lifecycle, window allocation and React Native initialization are managed by SceneDelegate.',
    );
  });

  it('is completely idempotent when run multiple times on already-transformed contents', () => {
    const pass1 = withIosSceneDelegate.injectAppDelegate(pristineAppDelegate);
    const pass2 = withIosSceneDelegate.injectAppDelegate(pass1);

    expect(pass1).toBe(pass2);

    const matches = pass2.match(/factory\.startReactNative/g) || [];
    expect(matches.length).toBe(1);
  });
});
