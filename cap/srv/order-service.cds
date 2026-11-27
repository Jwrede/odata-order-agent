using wholesale as db from '../db/schema';

// OData V4 service at /odata/v4/orders. Authorization is enforced here, in the ERP side,
// independent of what the agent or its tool server do:
//   OrderAgent  : read master data, create orders (always status Proposed), read own orders
//   AutoRelease : release Proposed orders up to the auto-release limit (checked in the handler)
//   Approver    : read all orders, release or reject them without limit
@requires: 'authenticated-user'
service OrderService @(path: '/odata/v4/orders') {

  // credit limit and open items stay inside the ERP; the agent never sees them
  @readonly
  entity Customers         as projection on db.Customers excluding { creditLimit, openItems };

  @readonly
  @restrict: [{ grant: 'READ', to: ['OrderAgent', 'Approver'] }]
  entity Contacts          as projection on db.Contacts;

  @readonly
  entity Products          as projection on db.Products;

  @readonly
  entity CustomerMaterials as projection on db.CustomerMaterials;

  @restrict: [
    { grant: ['READ', 'CREATE'], to: 'OrderAgent', where: 'createdBy = $user' },
    { grant: ['READ', 'release'], to: 'AutoRelease' },
    { grant: ['READ', 'release', 'decline'], to: 'Approver' }
  ]
  entity SalesOrders       as projection on db.SalesOrders actions {
    action release(note : String) returns SalesOrders;
    action decline(reason : String) returns SalesOrders;
  };

  @readonly
  entity SalesOrderItems   as projection on db.SalesOrderItems;
}
