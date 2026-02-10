import liff from '@line/liff';
import axios from 'axios';

// Configuration
// In production, these should be env vars or fetched from config
const LIFF_ID = import.meta.env.VITE_LIFF_ID || 'PENDING';
const ERPNEXT_URL = (import.meta.env.VITE_ERPNEXT_URL || window.location.origin.replace('liff.', '')).replace(/\/$/, ""); 
const API_BASE = `${ERPNEXT_URL}/api/method/line_integration.api.liff_api`;

const API_KEY = import.meta.env.VITE_API_KEY || '';
const API_SECRET = import.meta.env.VITE_API_SECRET || '';

// Axios Config
axios.defaults.headers.common['Content-Type'] = 'application/json';
axios.defaults.headers.common['Accept'] = 'application/json';

// Prevent 417 Expectation Failed & Add Auth Headers
axios.interceptors.request.use(config => {
  if (config.headers) {
    delete config.headers['Expect'];
    if (API_KEY && API_SECRET) {
        config.headers['Authorization'] = `token ${API_KEY}:${API_SECRET}`;
    }
  }
  return config;
});

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
    // console.log('Initializing LIFF...');
    await liff.init({ liffId: LIFF_ID });
    
    if (!liff.isLoggedIn()) {
      liff.login();
      return;
    }

    const accessToken = liff.getAccessToken();
    
    // Debug Ping
    try {
        await axios.get(`${API_BASE}.ping`);
    } catch (e) {
        console.warn('Backend debug ping failed:', e);
    }

    await authenticate(accessToken);
    setupNavigation();
    showPage('home');
    
  } catch (err) {
    console.error('LIFF Init Error:', err);
    showModal('ข้อผิดพลาด', 'เกิดข้อผิดพลาดในการโหลด LIFF: ' + err.message);
  }
}

/**
 * Authenticate with Frappe Backend
 */
async function authenticate(token) {
  if (!token) {
    console.warn('No access token available for auth');
    return;
  }
  try {
    const response = await axios.post(`${API_BASE}.liff_auth`, {
      access_token: token
    });
    
    const res = response.data.message;
    if (res && res.success) {
      user = res;
      // console.log('Authenticated User:', user);
      updateUIProfile();
      fetchPoints(); 
    } else {
      console.error('Auth API Error:', res?.error);
      // alert('ไม่สามารถยืนยันตัวตนได้: ' + (res?.error || 'Unknown error')); // Silent fail or show modal if critical
      loadingEl.classList.add('hidden'); 
    }
    
  } catch (err) {
    console.error('Auth Network/CORS Error:', err);
    // showModal('Connection Error', 'เกิดข้อผิดพลาดในการเชื่อมต่อเซิร์ฟเวอร์'); // Optional
    loadingEl.classList.add('hidden');
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
  updateCartBadge();
}

function updateCartBadge() {
  const totalQty = cart.reduce((sum, item) => sum + item.qty, 0);
  const badgeEl = document.getElementById('cart-badge');
  
  // Find cart nav item
  const cartNav = document.querySelector('[data-page="order"]');
  if (cartNav) {
      // Remove existing badge if any
      const existingBadge = cartNav.querySelector('.cart-badge');
      if (existingBadge) existingBadge.remove();
      
      if (totalQty > 0) {
          const badge = document.createElement('span');
          badge.className = 'cart-badge';
          badge.textContent = totalQty > 99 ? '99+' : totalQty;
          cartNav.appendChild(badge);
      }
  }
  updateFloatingCart();
}

function showPage(pageId) {
  contentEl.innerHTML = '';
  contentEl.classList.add('animated');
  
  // Hide floating cart on order page
  const floatBtn = document.getElementById('floating-cart-btn');
  if (floatBtn) {
      if (pageId === 'order') floatBtn.style.display = 'none';
      else floatBtn.style.display = 'block';
  }
  
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
    const response = await axios.get(`${API_BASE}.liff_get_menu`, {
        params: { access_token: liff.getAccessToken() }
    });
    menuItems = response.data.message;
    
    let html = '<div class="menu-grid">';
    menuItems.forEach(item => {
      const priceHtml = item.formatted_price 
        ? `<div class="price">${item.formatted_price}</div>` 
        : '';
        
      html += `
        <div class="item-card">
          <img src="${item.image_url || 'https://via.placeholder.com/200'}" class="item-image" />
          <div class="item-info">
            <div class="item-name">${item.item_name}</div>
            ${priceHtml}
            <div class="action-row">
                <div class="qty-selector">
                    <button class="qty-btn" onclick="adjustMenuQty('${item.item_code}', -1)">-</button>
                    <span id="qty-${item.item_code}" class="qty-display">1</span>
                    <button class="qty-btn" onclick="adjustMenuQty('${item.item_code}', 1)">+</button>
                </div>
                <button class="add-btn" onclick="addToCart('${item.item_code}')">ใส่ตะกร้า</button>
            </div>
          </div>
        </div>
      `;
    });
    html += '</div>';
    contentEl.innerHTML = html;
  } catch (err) {
    console.error(err);
    contentEl.innerHTML = '<p class="error">ไม่สามารถโหลดเมนูได้ กรุณาลองใหม่อีกครั้ง</p>';
  }
}

window.adjustMenuQty = (itemCode, delta) => {
    const qtyEl = document.getElementById(`qty-${itemCode}`);
    if (qtyEl) {
        let current = parseInt(qtyEl.textContent) || 1;
        current += delta;
        if (current < 1) current = 1;
        qtyEl.textContent = current;
    }
};

window.addToCart = (itemCode) => {
  const item = menuItems.find(i => i.item_code === itemCode);
  if (!item) return;
  
  const qtyEl = document.getElementById(`qty-${itemCode}`);
  const qty = parseInt(qtyEl ? qtyEl.textContent : 1) || 1;
  
  const existing = cart.find(c => c.item_code === itemCode);
  if (existing) {
    existing.qty += qty;
  } else {
    cart.push({ ...item, qty: qty });
  }
  
  // Update badge immediately
  updateCartBadge();
  updateFloatingCart();
  
  // Optional: Reset menu qty to 1
  if (qtyEl) qtyEl.textContent = '1';
  
  // Removed alert popup as requested
  // liff.sendMessages(...) // also removed to be completely silent/non-intrusive on UI
};

function updateFloatingCart() {
    let floatBtn = document.getElementById('floating-cart-btn');
    const totalQty = cart.reduce((sum, item) => sum + item.qty, 0);
    const grandTotal = cart.reduce((sum, item) => sum + ((item.price || 0) * item.qty), 0);
    
    if (totalQty === 0) {
        if (floatBtn) floatBtn.classList.add('hidden');
        return;
    }
    
    const formattedTotal = grandTotal.toLocaleString('th-TH', { style: 'currency', currency: 'THB' });
    
    if (!floatBtn) {
        floatBtn = document.createElement('div');
        floatBtn.id = 'floating-cart-btn';
        floatBtn.className = 'floating-cart-btn';
        floatBtn.onclick = () => showPage('order');
        document.body.appendChild(floatBtn);
    }
    
    floatBtn.innerHTML = `
        <div class="float-content">
            <div class="float-qty">${totalQty} รายการ</div>
            <div class="float-total">ไปตะกร้า ${formattedTotal} ></div>
        </div>
    `;
    floatBtn.classList.remove('hidden');
}

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
  let grandTotal = 0;
  
  cart.forEach((item, index) => {
    const itemTotal = (item.price || 0) * item.qty;
    grandTotal += itemTotal;
    const priceText = item.formatted_price ? `${item.formatted_price}/ชิ้น` : '';
    
    html += `
      <div class="cart-item">
        <div class="cart-item-info">
          <div class="name">${item.item_name}</div>
          <div class="price-detail">${priceText}</div>
        </div>
        <div class="qty-selector">
            <button class="qty-btn" onclick="adjustCartQty(${index}, -1)">-</button>
            <span class="qty-display">${item.qty}</span>
            <button class="qty-btn" onclick="adjustCartQty(${index}, 1)">+</button>
        </div>
        <button class="remove-btn" onclick="removeFromCart(${index})" style="margin-left: 10px;">x</button>
      </div>
    `;
  });
  
  const formattedTotal = grandTotal.toLocaleString('th-TH', { style: 'currency', currency: 'THB' });
  
  if (grandTotal > 0) {
      html += `<div class="cart-total"><h3>ยอดรวม: ${formattedTotal}</h3></div>`;
  }
  
  html += `</div>
    <div class="order-note">
      <textarea id="note" placeholder="หมายเหตุเพิ่มเติม (ถ้ามี)" class="input-field"></textarea>
    </div>
    <button class="btn btn-primary" id="submit-order-btn">สั่งออเดอร์ (${formattedTotal})</button>
  `;
  contentEl.innerHTML = html;
  
  document.getElementById('submit-order-btn').onclick = submitOrder;
  updateCartBadge();
}

window.adjustCartQty = (index, delta) => {
    cart[index].qty += delta;
    if (cart[index].qty < 1) cart[index].qty = 1;
    renderOrder();
    updateCartBadge();
};

window.removeFromCart = (index) => {
  cart.splice(index, 1);
  renderOrder();
  updateCartBadge();
};

async function submitOrder() {
  if (!user || !user.is_registered) {
    showModal('แจ้งเตือน', 'กรุณาสมัครสมาชิกก่อนสั่งออเดอร์', () => {
        document.querySelector('[data-page=profile]').click();
    });
    return;
  }

  showConfirm('ยืนยันออเดอร์', 'คุณต้องการยืนยันการสั่งซื้อหรือไม่?', async () => {
    const note = document.getElementById('note').value;
    try {
      // Show loading state
      const btn = document.getElementById('submit-order-btn');
      const originalText = btn.textContent;
      btn.textContent = 'กำลังทำรายการ...';
      btn.disabled = true;

      const response = await axios.post(`${API_BASE}.liff_submit_order`, {
        access_token: liff.getAccessToken(),
        items: cart,
        note: note
      });
      
      showModal('สำเร็จ', `สั่งออเดอร์เรียบร้อย! เลขที่: ${response.data.message.sales_order}`);
      cart = [];
      showPage('home');
      updateCartBadge();
    } catch (err) {
      showModal('ผิดพลาด', 'สั่งออเดอร์ล้มเหลว: ' + (err.response?.data?.message || err.message));
      // Reset button
      const btn = document.getElementById('submit-order-btn');
      if(btn) {
          btn.textContent = originalText;
          btn.disabled = false;
      }
    }
  });
}

async function renderProfile() {
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
    // Basic Profile
    let html = `
      <div class="profile-card">
        <img src="${user.picture_url}" class="profile-img-large" />
        <h2>${user.customer_name}</h2>
        <p class="phone">📞 ${user.phone}</p>
        <div class="points-display">
          <div class="points-val">${userPointsEl.textContent}</div>
          <div class="points-label">แต้มสะสมปัจจุบัน</div>
        </div>
      </div>
      
      <div class="order-history-section">
          <h3 class="section-title">ประวัติการสั่งซื้อล่าสุด</h3>
          <div id="history-loading" class="loader-container" style="height: 100px;">
              <div class="loader" style="width: 30px; height: 30px; border-width: 3px;"></div>
          </div>
          <div id="history-list"></div>
      </div>
    `;
    contentEl.innerHTML = html;
    
    // Fetch History
    try {
        const response = await axios.post(`${API_BASE}.liff_get_history`, {
             access_token: liff.getAccessToken()
        });
        const orders = response.data.message || [];
        const historyList = document.getElementById('history-list');
        document.getElementById('history-loading').style.display = 'none';
        
        if (orders.length === 0) {
            historyList.innerHTML = '<p class="text-center text-muted">ยังไม่มีประวัติการสั่งซื้อ</p>';
            return;
        }
        
        const statusMap = {
            'To Deliver and Bill': 'รอจัดส่ง',
            'To Bill': 'รอชำระ',
            'To Deliver': 'รอจัดส่ง',
            'Completed': 'สำเร็จ',
            'Cancelled': 'ยกเลิก',
            'Overdue': 'เกินกำหนด'
        };

        let listHtml = '';
        orders.forEach(order => {
            let itemsHtml = '';
            if (order.items && order.items.length > 0) {
                itemsHtml = `<div class="history-items hidden" id="order-items-${order.name}">`;
                order.items.forEach(item => {
                    itemsHtml += `
                        <div class="history-item-row">
                            <span class="item-name">${item.item_name}</span>
                            <span class="item-qty">x${item.formatted_qty}</span>
                        </div>
                    `;
                });
                itemsHtml += '</div>';
            }

            const statusClass = order.status || 'Draft';
            const statusLabel = statusMap[order.status] || order.status;

            listHtml += `
                <div class="history-card status-${statusClass}" onclick="toggleOrderDetails('${order.name}')">
                    <div class="history-card-header">
                        <div class="history-info">
                            <h4>${order.name}</h4>
                            <div class="history-date">${date}</div>
                        </div>
                        <div class="history-status">
                            <span class="status-badge ${statusClass}">${statusLabel}</span>
                            <div class="history-total">${order.formatted_total}</div>
                        </div>
                    </div>
                    ${itemsHtml}
                </div>
            `;
        });
        historyList.innerHTML = listHtml;
        
    } catch (err) {
        console.error("History Error", err);
        document.getElementById('history-loading').innerHTML = '<p class="error">โหลดประวัติไม่สำเร็จ</p>';
    }
  }
}

function toggleOrderDetails(orderId) {
    const el = document.getElementById(`order-items-${orderId}`);
    if (el) {
        el.classList.toggle('hidden');
    }
}

async function register() {
  const phone = document.getElementById('reg-phone').value;
  if (!/^\d{10}$/.test(phone)) {
    showModal('แจ้งเตือน', 'กรุณาระบุหมายเลขโทรศัพท์ 10 หลักให้ถูกต้อง');
    return;
  }

  try {
    const response = await axios.post(`${API_BASE}.liff_register`, {
      access_token: liff.getAccessToken(),
      phone: phone
    });
    
    showModal('สำเร็จ', 'ลงทะเบียนเรียบร้อยแล้ว!');
    await authenticate(liff.getAccessToken()); // re-auth to get customer info
    renderProfile();
  } catch (err) {
    showModal('ผิดพลาด', 'ลงทะเบียนล้มเหลว: ' + (err.response?.data?.message || err.message));
  }
}

// Modal Helpers
function setupModals() {
    const modalHtml = `
        <div id="custom-modal" class="modal-overlay">
            <div class="modal-content">
                <div class="modal-title" id="modal-title"></div>
                <div class="modal-body" id="modal-body"></div>
                <div class="modal-actions" id="modal-actions"></div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

function showModal(title, message, onConfirm = null) {
    const modal = document.getElementById('custom-modal');
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').textContent = message;
    
    const actions = document.getElementById('modal-actions');
    actions.innerHTML = '';
    
    const btn = document.createElement('button');
    btn.className = 'modal-btn primary';
    btn.textContent = 'ตกลง';
    btn.onclick = () => {
        closeModal();
        if (onConfirm) onConfirm();
    };
    actions.appendChild(btn);
    
    modal.classList.add('active');
}

function showConfirm(title, message, onConfirm) {
    const modal = document.getElementById('custom-modal');
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').textContent = message;
    
    const actions = document.getElementById('modal-actions');
    actions.innerHTML = '';
    
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn secondary';
    cancelBtn.textContent = 'ยกเลิก';
    cancelBtn.onclick = closeModal;
    
    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'modal-btn primary';
    confirmBtn.textContent = 'ยืนยัน';
    confirmBtn.onclick = () => {
        closeModal();
        if (onConfirm) onConfirm();
    };
    
    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    
    modal.classList.add('active');
}

function closeModal() {
    document.getElementById('custom-modal').classList.remove('active');
}

// Start app
setupModals();
init();
