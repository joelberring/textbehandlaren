// Firebase configuration - fetched dynamically from backend
let firebaseConfig = null;
let auth = null;

// Initialize Firebase configuration
async function initializeFirebase() {
    try {
        const response = await fetch('/api/config/firebase');
        if (!response.ok) {
            throw new Error('Failed to fetch Firebase configuration');
        }
        firebaseConfig = await response.json();

        // Initialize Firebase
        firebase.initializeApp(firebaseConfig);
        auth = firebase.auth();

        // Setup auth state observer
        setupAuthObserver();

        console.log("Firebase initialized successfully");
    } catch (error) {
        console.error("Firebase initialization failed:", error);
    }
}

// Call initialization
initializeFirebase();

// Current user state
let currentUser = null;
let idToken = null;

// Auth/token observer setup (also fires on token refresh)
function setupAuthObserver() {
    auth.onIdTokenChanged(async (user) => {
        currentUser = user;
        if (user) {
            idToken = await user.getIdToken();
            updateUIForLoggedInUser(user);
        } else {
            idToken = null;
            updateUIForLoggedOutUser();
        }
    });
}

// Login with email/password
async function loginWithEmail(email, password) {
    try {
        const result = await auth.signInWithEmailAndPassword(email, password);
        return result.user;
    } catch (error) {
        console.error("Login error:", error);
        throw error;
    }
}

// Login with Google
async function loginWithGoogle() {
    try {
        const provider = new firebase.auth.GoogleAuthProvider();
        const result = await auth.signInWithPopup(provider);
        return result.user;
    } catch (error) {
        console.error("Google login error:", error);
        throw error;
    }
}

// Register new user
async function registerWithEmail(email, password) {
    try {
        const result = await auth.createUserWithEmailAndPassword(email, password);
        return result.user;
    } catch (error) {
        console.error("Registration error:", error);
        throw error;
    }
}

// Logout
async function logout() {
    try {
        await auth.signOut();
    } catch (error) {
        console.error("Logout error:", error);
    }
}

// Reset password
async function resetPassword(email) {
    if (!email) {
        email = prompt('Ange din e-postadress:');
    }
    if (!email) return;
    try {
        await auth.sendPasswordResetEmail(email);
        alert('Ett återställningsmejl har skickats till ' + email + '. Kolla din inkorg (och skräppost).');
    } catch (error) {
        console.error('Password reset error:', error);
        if (error.code === 'auth/user-not-found') {
            alert('Ingen användare hittades med den e-postadressen.');
        } else if (error.code === 'auth/invalid-email') {
            alert('Ogiltig e-postadress.');
        } else {
            alert('Kunde inte skicka återställningsmejl: ' + error.message);
        }
    }
}

// Get auth header for API requests
function getAuthHeaders(contentType = 'application/json') {
    const headers = {};
    if (idToken) {
        headers['Authorization'] = `Bearer ${idToken}`;
    }
    if (contentType) {
        headers['Content-Type'] = contentType;
    }
    return headers;
}

// UI update functions
function updateUIForLoggedInUser(user) {
    const authSection = document.getElementById('auth-section');
    if (authSection) {
        authSection.innerHTML = `
            <div class="user-info">
                <span class="user-email">${user.email}</span>
                <button onclick="logout()" class="btn btn-small">Logga ut</button>
            </div>
        `;
    }
    // Enable app functionality
    document.querySelectorAll('.requires-auth').forEach(el => {
        el.style.display = 'block';
    });
    // Role-specific sections are unlocked after backend profile is loaded.
    document.querySelectorAll('.requires-superadmin').forEach(el => {
        el.style.display = 'none';
    });
}

function updateUIForLoggedOutUser() {
    const authSection = document.getElementById('auth-section');
    if (authSection) {
        authSection.innerHTML = `
            <button onclick="showLoginModal()" class="btn">Logga in</button>
        `;
    }
    // Disable app functionality
    document.querySelectorAll('.requires-auth').forEach(el => {
        el.style.display = 'none';
    });
    document.querySelectorAll('.requires-superadmin').forEach(el => {
        el.style.display = 'none';
    });
}

// Show login modal
function showLoginModal() {
    const modal = document.getElementById('login-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function hideLoginModal() {
    const modal = document.getElementById('login-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// Handle login form submission
async function handleLogin(event) {
    event.preventDefault();
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;

    try {
        await loginWithEmail(email, password);
        hideLoginModal();
    } catch (error) {
        alert('Inloggning misslyckades: ' + error.message);
    }
}

// Handle registration
async function handleRegister(event) {
    event.preventDefault();
    const email = document.getElementById('register-email').value;
    const password = document.getElementById('register-password').value;

    try {
        await registerWithEmail(email, password);
        hideLoginModal();
    } catch (error) {
        alert('Registrering misslyckades: ' + error.message);
    }
}
