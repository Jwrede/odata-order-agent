// Business rules of the order service. They hold for every caller, so an agent cannot bypass
// them by calling the OData API directly.
const cds = require('@sap/cds')

const AUTO_RELEASE_LIMIT = Number(process.env.AUTO_RELEASE_LIMIT || 2500)
const today = () => process.env.ORDER_TODAY || new Date().toISOString().slice(0, 10)
const round = n => Math.round(n * 100) / 100

module.exports = class OrderService extends cds.ApplicationService {
  init() {
    const { SalesOrders } = this.entities
    const db = cds.entities('wholesale')

    this.before('CREATE', SalesOrders, async req => {
      const d = req.data
      // status, prices and decisions are set by the ERP, never by the caller
      for (const f of ['status', 'releasedBy', 'releasedAt', 'decisionNote', 'netAmount']) delete d[f]
      d.status = 'Proposed'
      const cust = await SELECT.one.from(db.Customers).where({ ID: d.customer_ID })
      if (!cust) return req.reject(400, `unknown customer ${d.customer_ID}`)
      if (cust.blocked) return req.reject(403, `customer ${cust.ID} is blocked for new orders`)
      if (!d.customerReference) return req.reject(400, 'customerReference (purchase order number of the customer) is required')
      const dup = await SELECT.one.from(db.SalesOrders)
        .where({ customer_ID: cust.ID, customerReference: d.customerReference, status: { '!=': 'Rejected' } })
      if (dup) return req.reject(409, `duplicate: an order for ${cust.ID} with reference ${d.customerReference} already exists (status ${dup.status})`)
      if (d.requestedDate && d.requestedDate < today()) return req.reject(400, `requestedDate ${d.requestedDate} is in the past (today is ${today()})`)
      if (!Array.isArray(d.items) || d.items.length === 0) return req.reject(400, 'an order needs at least one item')
      let total = 0
      let pos = 0
      for (const it of d.items) {
        pos += 10
        it.pos = pos
        const p = await SELECT.one.from(db.Products).where({ ID: it.product_ID })
        if (!p) return req.reject(400, `unknown product ${it.product_ID}`)
        if (p.discontinued) return req.reject(400, `product ${p.ID} is discontinued`)
        if (!Number.isInteger(it.quantity) || it.quantity <= 0 || it.quantity > 10000) return req.reject(400, `invalid quantity ${it.quantity} for ${p.ID}`)
        if (it.quantity > p.stock) return req.reject(409, `insufficient stock for ${p.ID}: requested ${it.quantity} ${p.unit}, available ${p.stock}`)
        it.unitPrice = Number(p.listPrice)
        it.netAmount = round(it.quantity * it.unitPrice)
        total += it.netAmount
      }
      d.netAmount = round(total)
      if (Number(cust.openItems) + d.netAmount > Number(cust.creditLimit)) return req.reject(422, `credit limit exceeded for customer ${cust.ID}; needs credit management`)
    })

    this.on('release', SalesOrders, async req => {
      const ID = keyOf(req)
      const o = await SELECT.one.from(db.SalesOrders).where({ ID })
      if (!o) return req.reject(404, `order ${ID} not found`)
      if (o.status !== 'Proposed') return req.reject(409, `order ${ID} is ${o.status}, only Proposed orders can be released`)
      if (!req.user.is('Approver')) {
        if (Number(o.netAmount) > AUTO_RELEASE_LIMIT) return req.reject(403, `order value ${o.netAmount} EUR is above the auto-release limit of ${AUTO_RELEASE_LIMIT} EUR; needs an Approver`)
        const c = await SELECT.one.from(db.Customers).where({ ID: o.customer_ID })
        if (c.newCustomer) return req.reject(403, `first order of new customer ${c.ID} needs an Approver`)
      }
      await UPDATE(db.SalesOrders, ID).with({ status: 'Released', releasedBy: req.user.id, releasedAt: new Date().toISOString(), decisionNote: req.data.note ?? null })
      return SELECT.one.from(db.SalesOrders).where({ ID })
    })

    this.on('decline', SalesOrders, async req => {
      const ID = keyOf(req)
      const o = await SELECT.one.from(db.SalesOrders).where({ ID })
      if (!o) return req.reject(404, `order ${ID} not found`)
      if (o.status !== 'Proposed') return req.reject(409, `order ${ID} is ${o.status}, only Proposed orders can be declined`)
      if (!req.data.reason) return req.reject(400, 'a reason is required')
      await UPDATE(db.SalesOrders, ID).with({ status: 'Rejected', releasedBy: req.user.id, releasedAt: new Date().toISOString(), decisionNote: req.data.reason })
      return SELECT.one.from(db.SalesOrders).where({ ID })
    })

    return super.init()
  }
}

function keyOf(req) {
  const p = req.params[req.params.length - 1]
  return typeof p === 'object' ? p.ID : p
}
