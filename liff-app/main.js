import liff from '@line/liff';
import axios from 'axios';

// Configuration
// In production, these should be env vars or fetched from config
const LIFF_ID = import.meta.env.VITE_LIFF_ID || 'PENDING';
const ERPNEXT_URL = import.meta.env.VITE_ERPNEXT_URL || window.location.origin.replace('liff.', ''); 
const API_BASE = `${ERPNEXT_URL}/api/method/line_integration.line_integration.api.liff_api`;

// State
let user = null;
let cart = [];
let menuItems = [];

// DOM Elements
const loadingEl = document.getElementById('loading');
const headerEl = document.getElementById('main-header');
const contentEl = document.getElementById('content');
const navEl = document.getElementById('bottom-nav');
const userImgEl = document.getElementById('user-img');
const userNameEl = document.getElementById('user-name');
const userPointsEl = document.getElementById('user-points');

/**
 * Initialize LIFF
 */
async function init() {
  try {
    console.log('Initializing LIFF...');
    await liff.init({ liffId: LIFF_ID });
    
    if (!liff.isLoggedIn()) {
      liff.login();
      return;
    }

    const accessToken = liff.getAccessToken();
    await authenticate(accessToken);
    setupNavigation();
    showPage('home');
    
  } catch (err) {
    console.error('LIFF Init Error:', err);
    alert('เกิดข้อผิดพลาดในการโหลด LIFF: ' + err.message);
  }
}

/**
 * Authenticate with Frappe Backend
 */
async function authenticate(token) {
  try {
    const response = await axios.post(`${API_BASE}.liff_auth`, {
      access_token: token
    });
    
    user = response.data.message;
    console.log('Authenticated User:', user);
    
    updateUIProfile();
    fetchPoints(); // silent fetch points
    
  } catch (err) {
    console.error('Auth Error:', err);
    // If auth fails, user might not be registered or API issue
    // We still show Home but with restricted actions
  }
}

function updateUIProfile() {
  userImgEl.src = user.picture_url || 'https://via.placeholder.com/40';
  userNameEl.textContent = user.display_name || 'สวัสตีครับ';
  loadingEl.classList.add('hidden');
  headerEl.classList.remove('hidden');
  contentEl.classList.remove('hidden');
  navEl.classList.remove('hidden');
}

async function fetchPoints() {
  if (!user || !user.is_registered) return;
  try {
    const response = await axios.post(`${API_BASE}.liff_get_points`, {
      access_token: liff.getAccessToken()
    });
    const pointsData = response.data.message;
    userPointsEl.textContent = pointsData.points || 0;
  } catch (err) {
    console.error('Fetch Points Error:', err);
  }
}

/**
 * Navigation logic
 */
function setupNavigation() {
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      showPage(btn.dataset.page);
    });
  });
}

function showPage(pageId) {
  contentEl.innerHTML = '';
  contentEl.classList.add('animated');
  
  switch(pageId) {
    case 'home': renderHome(); break;
    case 'menu': renderMenu(); break;
    case 'order': renderOrder(); break;
    case 'profile': renderProfile(); break;
  }
  
  // Clean up animation class after it runs
  setTimeout(() => contentEl.classList.remove('animated'), 500);
}

/**
 * Page Renders
 */

function renderHome() {
  contentEl.innerHTML = `
    <div class="home-hero">
      <h1>ยินดีต้อนรับสู่ Wellie</h1>
      <p>เลือกเมนูที่ถูกใจและสั่งซื้อได้ทันทีผ่าน LIFF</p>
    </div>
    
    <div class="quick-actions">
      <div class="action-card" onclick="document.querySelector('[data-page=menu]').click()">
        <span class="action-icon">📜</span>
        <h3>ดูเมนูและสั่งอาหาร</h3>
      </div>
      <div class="action-card" onclick="document.querySelector('[data-page=profile]').click()">
        <span class="action-icon">👤</span>
        <h3>ข้อมูลสมาชิก / แต้ม</h3>
      </div>
    </div>
  `;
}

async function renderMenu() {
  contentEl.innerHTML = '<div class="loader-container"><div class="loader"></div><p>กำลังโหลดเมนู...</p></div>';
  
  try {
    const response = await axios.get(`${API_BASE}.liff_get_menu`);
    menuItems = response.data.message;
    
    let html = '<div class="menu-grid">';
    menuItems.forEach(item => {
      html += `
        <div class="item-card">
          <img src="${item.image_url || 'https://via.placeholder.com/200'}" class="item-image" />
          <div class="item-info">
            <div class="item-name">${item.item_name}</div>
            <button class="add-btn" onclick="addToCart('${item.item_code}')">เพิ่มลงตะกร้า</button>
          </div>
        </div>
      `;
    });
    html += '</div>';
    contentEl.innerHTML = html;
  } catch (err) {
    contentEl.innerHTML = '<p class="error">ไม่สามารถโหลดเมนูได้ กรุณาลองใหม่อีกครั้ง</p>';
  }
}

window.addToCart = (itemCode) => {
  const item = menuItems.find(i => i.item_code === itemCode);
  if (!item) return;
  
  const existing = cart.find(c => c.item_code === itemCode);
  if (existing) {
    existing.qty += 1;
  } else {
    cart.push({ ...item, qty: 1 });
  }
  alert(`เพิ่ม ${item.item_name} ลงตะกร้าแล้ว`);
};

function renderOrder() {
  if (cart.length === 0) {
    contentEl.innerHTML = `
      <div class="empty-state">
        <span class="icon">🛒</span>
        <p>ยังไม่มีสินค้าในตะกร้า</p>
        <button class="btn btn-primary" onclick="document.querySelector('[data-page=menu]').click()">ไปเลือกเมนู</button>
      </div>
    `;
    return;
  }

  let html = '<h2>ตะกร้าสินค้า</h2><div class="cart-items">';
  cart.forEach((item, index) => {
    html += `
      <div class="cart-item">
        <div class="cart-item-info">
          <div class="name">${item.item_name}</div>
          <div class="qty">จำนวน: ${item.qty}</div>
        </div>
        <button class="remove-btn" onclick="removeFromCart(${index})">ลบ</button>
      </div>
    `;
  });
  html += `</div>
    <div class="order-note">
      <textarea id="note" placeholder="หมายเหตุเพิ่มเติม (ถ้ามี)" class="input-field"></textarea>
    </div>
    <button class="btn btn-primary" id="submit-order-btn">สั่งออเดอร์เลย</button>
  `;
  contentEl.innerHTML = html;
  
  document.getElementById('submit-order-btn').onclick = submitOrder;
}

window.removeFromCart = (index) => {
  cart.splice(index, 1);
  renderOrder();
};

async function submitOrder() {
  if (!user || !user.is_registered) {
    alert('กรุณาสมัครสมาชิกก่อนสั่งออเดอร์');
    document.querySelector('[data-page=profile]').click();
    return;
  }

  if (confirm('ยืนยันการสั่งออเดอร์?')) {
    const note = document.getElementById('note').value;
    try {
      const response = await axios.post(`${API_BASE}.liff_submit_order`, {
        access_token: liff.getAccessToken(),
        items: cart,
        note: note
      });
      
      alert(`สั่งออเดอร์เรียบร้อย! เลขที่ใบสั่งซื้อ: ${response.data.message.sales_order}`);
      cart = [];
      showPage('home');
    } catch (err) {
      alert('สั่งออเดอร์ล้มเหลว: ' + (err.response?.data?.message || err.message));
    }
  }
}

function renderProfile() {
  if (!user.is_registered) {
    contentEl.innerHTML = `
      <h2>สมัครสมาชิก</h2>
      <p>สมัครสมาชิกเพื่อสั่งออเดอร์และสะสมแต้ม</p>
      <div class="form-group">
        <label>หมายเลขโทรศัพท์ (10 หลัก)</label>
        <input type="tel" id="reg-phone" class="input-field" placeholder="08XXXXXXXX" maxlength="10" />
      </div>
      <button class="btn btn-primary" id="reg-btn">ลงทะเบียน</button>
    `;
    document.getElementById('reg-btn').onclick = register;
  } else {
    contentEl.innerHTML = `
      <div class="profile-card">
        <img src="${user.picture_url}" class="profile-img-large" />
        <h2>${user.customer_name}</h2>
        <p class="phone">📞 ${user.phone}</p>
        <div class="points-display">
          <div class="points-val">${userPointsEl.textContent}</div>
          <div class="points-label">แต้มสะสมปัจจุบัน</div>
        </div>
      </div>
    `;
  }
}

async function register() {
  const phone = document.getElementById('reg-phone').value;
  if (!/^\d{10}$/.test(phone)) {
    alert('กรุณาระบุหมายเลขโทรศัพท์ 10 หลักให้ถูกต้อง');
    return;
  }

  try {
    const response = await axios.post(`${API_BASE}.liff_register`, {
      access_token: liff.getAccessToken(),
      phone: phone
    });
    
    alert('ลงทะเบียนเรียบร้อยแล้ว!');
    await authenticate(liff.getAccessToken()); // re-auth to get customer info
    renderProfile();
  } catch (err) {
    alert('ลงทะเบียนล้มเหลว: ' + (err.response?.data?.message || err.message));
  }
}

// Start app
init();
