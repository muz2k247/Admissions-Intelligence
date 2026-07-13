import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup, signOut } from "firebase/auth";
import { auth, googleProvider } from "../firebase";

/* Firebase Auth state.
 * user === undefined -> still resolving the initial auth state (show a
 *                       loading state, not the sign-in screen, to avoid a
 *                       flash of "signed out" for an already-signed-in user).
 * user === null      -> signed out.
 * user (object)      -> signed in. */
export default function useAuth() {
  const [user, setUser] = useState(undefined);

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  const signIn = () => signInWithPopup(auth, googleProvider);
  const logOut = () => signOut(auth);

  return { user, signIn, logOut };
}
