import useAuth from "./hooks/useAuth";
import SignIn from "./components/SignIn";
import ReviewScreen from "./components/ReviewScreen";

export default function App() {
  const { user, signIn, logOut } = useAuth();

  // undefined = auth state still resolving; avoid flashing the sign-in screen
  // for a user who is actually already signed in.
  if (user === undefined) {
    return <div className="screen screen--center muted">Loading…</div>;
  }
  if (!user) {
    return <SignIn onSignIn={signIn} />;
  }
  return <ReviewScreen user={user} onLogOut={logOut} />;
}
