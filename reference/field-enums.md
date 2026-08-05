# Field Enums

Quick reference for numeric enum values used across entities.
Values validated against live Nexudus API schemas on 2026-08-05.

## eTariffType — SystemTariffType (Tariff)
| Value | Name |
|-------|------|
| 1 | FullTimePrivateOffice |
| 2 | PartTimePrivateOffice |
| 3 | FullTimeDedicatedDesk |
| 4 | PartTimeDedicatedDesk |
| 5 | FullTimeHotDesk |
| 6 | PartTimeHotDesk |
| 7 | FullTimeOther |
| 8 | PartTimeOther |
| 9 | Storage |
| 10 | VirtualOffice |
| 11 | Virtual |
| 99 | Other |

## eResourceType — SystemResourceType (Resource)
Note: ResourceType is also a separate entity (just BusinessId + Name).
The `SystemResourceType` enum on Resource is distinct from the ResourceType FK.

| Value | Name |
|-------|------|
| 1 | MeetingRoom |
| 2 | HotDesk |
| 3 | PrivateOffice |
| 4 | PhoneBooth |
| 5 | Parking |

## eFloorPlanItemType — ItemType (FloorPlanDesk)
| Value | Name |
|-------|------|
| 1 | Office |
| 2 | DedicatedDesk |
| 3 | HotDesk |
| 4 | Other |
| 5 | Room |

## eRecurrentProductOptions — AvailableAs (Product)
| Value | Name |
|-------|------|
| 1 | RecurrentOrOneOff |
| 2 | OnlyRecurrent |
| 3 | OnlyOneOff |

## eProductType — SystemProductType (Product)
| Value | Name |
|-------|------|
| 1 | DayPass |
| 2 | CreditBundle |
| 3 | Stationery |
| 4 | BookingFeature |
| 5 | BookingProducts |
| 6 | Lockers |
| 7 | Equipment |
| 8 | EventServices |
| 9 | AdminServices |
| 10 | FoodAndBeverage |
| 99 | Other |

## eFinancialAccountType — AccountType (FinancialAccount)
| Value | Name |
|-------|------|
| 1 | Sales |
| 2 | Payments |
| 3 | Deposits |

## eTaxExemptionReason — ExemptionReason (TaxRate)
| Value | Name | Notes |
|-------|------|-------|
| 1 | None | Use for standard/reduced/zero-rated taxes |
| 2–30 | M01–M99 | Various EU exemption codes |

## eDeliveryType — DeliveryType (CoworkerDelivery)
| Value | Name |
|-------|------|
| 1 | Mail |
| 2 | Parcel |
| 3 | Check |
| 4 | Publicity |
| 5 | Other |

## eDeliveryHandlingPreference (Tariff default / CoworkerDelivery)
| Value | Name |
|-------|------|
| 1 | StoreForCollection |
| 2 | Forward |
| 3 | OpenScanForward |
| 4 | OpenScanRecycle |
| 5 | OpenScanShred |
| 6 | OpenScanStoreForCollection |
| 7 | Recycle |
| 8 | ReturnToSender |
| 9 | Shred |
| 10 | DepositCheck |
| 11 | Unknown |

## eProposalStatus — ProposalStatus (Proposal)
| Value | Name |
|-------|------|
| 1 | Draft |
| 2 | Sent |
| 3 | Accepted |
| 4 | Rejected |

## Repeats (Booking / CalendarEvent)
| Value | Name |
|-------|------|
| 1 | Daily |
| 2 | Weekly |
| 3 | Monthly |

## eAssignToType — AssignToType (InventoryAsset)
| Value | Name |
|-------|------|
| 1 | Location |
| 2 | Resource |
| 3 | FloorPlanItem |

## eVisitorSource — VisitorSource (Visitor)
| Value | Name |
|-------|------|
| 1 | Administrator |
| 2 | NexIO |
| 3 | Customer |

## eVisitorHostApprovalStatus — HostApprovalStatus (Visitor)
| Value | Name |
|-------|------|
| 1 | NotRequired |
| 2 | Requested |
| 3 | Rejected |
| 4 | AcceptedAndHold |
| 5 | AcceptedAndGrant |

## eCoworkerRecordType — CoworkerType (Coworker)
| Value | Name |
|-------|------|
| 1 | Individual |
| 2 | Company |

## eGender — Gender (Coworker)
| Value | Name |
|-------|------|
| 1 | NotSet |
| 2 | Male |
| 3 | Female |
| 4 | Other |
| 5 | RatherNotSay |

## eCoworkerAttendance — Day Attendance (Coworker)
| Value | Name |
|-------|------|
| 1 | WorkingFromOffice |
| 2 | WorkingFromHome |
| 3 | WorkingFromAbroad |
| 4 | NotWorking |
| 5 | Undefined |

---

## Required Fields by Entity (non-obvious)

These are required fields that may not be immediately obvious from the strategy doc.

| Entity | Required fields beyond BusinessId + Name |
|--------|----------------------------------------|
| TaxRate | `Rate`, `ExemptionReason` (use 1=None) |
| Tariff | `CurrencyId`, `Price`, `SystemTariffType`, `InvoiceEvery`, `InvoiceEveryWeeks`, `CancellationPeriod`, `TotalSignUpPrice`, `TotalPrice` |
| Product | `Description`, `DisplayOrder`, `Price`, `CurrencyId`, `SystemProductType`, `AvailableAs` |
| ExtraService | `DisplayOrder`, `Price`, `ChargePeriod`, `CurrencyId`, `LastMinuteAdjustmentType` |
| TimePass | `Price`, `CurrencyId` |
| Resource | `SystemResourceType`, `ResourceTypeId`, `DisplayOrder`, `CancellationFeeType` |
| FloorPlan | `BackgroundScale`, `PositionX`, `PositionY`, `FloorLevel`, `Scale` |
| FloorPlanDesk | `FloorPlanId`, `ItemType`, `Size`, `Capacity`, `Price`, `PositionX`, `PositionY`, `PositionZ` |
| DiscountCode | `Code`, `Description` |
| Team | `ActiveContracts` (int, set to 0 on create) |
