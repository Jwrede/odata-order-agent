namespace wholesale;

using { cuid, managed } from '@sap/cds/common';

// Master data of a fictitious technical wholesaler (all data synthetic).
entity Customers {
  key ID          : String(10);
      name        : String(100);
      city        : String(60);
      creditLimit : Decimal(12, 2); // not exposed to the agent
      openItems   : Decimal(12, 2); // receivables not yet paid, not exposed to the agent
      blocked     : Boolean default false;
      newCustomer : Boolean default false; // no order released yet
}

// Mail addresses that are known purchasing contacts of a customer.
entity Contacts {
  key email    : String(120);
      customer : Association to Customers;
}

entity Products {
  key ID           : String(20);
      name         : String(120);
      description  : String(500);
      unit         : String(10);  // sales unit, e.g. CT (carton), PAC, PC
      packSize     : Integer;     // pieces per sales unit
      listPrice    : Decimal(10, 2); // EUR per sales unit
      stock        : Integer;     // available sales units
      discontinued : Boolean default false;
}

// Customer-specific material numbers (SAP: customer-material info record).
entity CustomerMaterials {
  key customer         : Association to Customers;
  key customerMaterial : String(40);
      product          : Association to Products;
}

entity SalesOrders : cuid, managed {
  customer          : Association to Customers;
  customerReference : String(40);  // the customer's purchase order number
  requestedDate     : Date;
  status            : String(10) default 'Proposed'; // Proposed | Released | Rejected
  netAmount         : Decimal(12, 2);
  sourceMessage     : String(80);  // id of the mail the order came from
  agentNote         : String(1000);
  releasedBy        : String(80);
  releasedAt        : Timestamp;
  decisionNote      : String(500);
  items             : Composition of many SalesOrderItems on items.order = $self;
}

entity SalesOrderItems {
  key order     : Association to SalesOrders;
  key pos       : Integer;
      product   : Association to Products;
      quantity  : Integer;
      unitPrice : Decimal(10, 2);
      netAmount : Decimal(12, 2);
}
